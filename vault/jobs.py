"""Startup setup and the built-in daily job for hosted deployments.

On a host with one persistent disk (Railway volume, Render disk), a separate
scheduled service can't reach the app's storage, so the daily price refresh and
value snapshot run inside the app process instead.
"""
import fcntl
import logging
import os
import threading
import time
from datetime import datetime, timezone

from . import db
from .pricing import get_source, item_value, sync_catalog

log = logging.getLogger("vault")

# Run the daily job once per UTC day, at or after this hour (9 UTC = 3–4 a.m. Central).
DAILY_HOUR_UTC = int(os.environ.get("DAILY_HOUR_UTC", "9"))


def ensure_ready():
    """Create tables; load the catalog and a demo customer on first start."""
    db.init_db()
    with db.session() as conn:
        conn.execute("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)")
        if not conn.execute("SELECT 1 FROM catalog_cards LIMIT 1").fetchone():
            cards, prices = sync_catalog(conn, get_source())
            log.info("Loaded catalog: %s cards, %s prices", cards, prices)
        if not conn.execute("SELECT 1 FROM customers LIMIT 1").fetchone():
            conn.execute("INSERT INTO customers (name, email, created_at) VALUES (?, ?, ?)",
                         ("Demo Collector", "demo@example.com", db.now_iso()))


def snapshot(conn, day=None):
    day = day or db.today()
    items = conn.execute("SELECT * FROM vault_items WHERE status != 'shipped'").fetchall()
    for it in items:
        conn.execute("INSERT OR REPLACE INTO value_snapshots (vault_item_id, day, value) VALUES (?, ?, ?)",
                     (it["id"], day, item_value(conn, it)))
    return len(items)


def run_daily(refresh_prices=True):
    with db.session() as conn:
        if refresh_prices:
            cards, prices = sync_catalog(conn, get_source())
            log.info("Prices refreshed: %s cards, %s prices", cards, prices)
        n = snapshot(conn)
        conn.execute("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)")
        conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('last_daily', ?)", (db.today(),))
    log.info("Saved today's value for %s vault items", n)
    return n


def _due():
    if datetime.now(timezone.utc).hour < DAILY_HOUR_UTC:
        return False
    with db.session() as conn:
        conn.execute("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)")
        row = conn.execute("SELECT value FROM meta WHERE key = 'last_daily'").fetchone()
    return not row or row["value"] != db.today()


def _loop(interval):
    lock_path = os.path.join(os.path.dirname(db.db_path()) or ".", ".daily.lock")
    while True:
        try:
            with open(lock_path, "w") as lock:
                try:
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)  # one runner even with several workers
                except BlockingIOError:
                    pass
                else:
                    if _due():
                        run_daily()
        except Exception:  # keep the scheduler alive; the next tick retries
            log.exception("Daily job failed")
        time.sleep(interval)


def start_scheduler(interval=900):
    t = threading.Thread(target=_loop, args=(interval,), name="vault-daily", daemon=True)
    t.start()
    return t
