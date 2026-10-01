"""Daily job: refresh prices from the price source, then save today's value for every vault item.

    python3 scripts/daily.py                      # uses PRICE_SOURCE (default: the seed file)
    PRICE_SOURCE=shopify python3 scripts/daily.py # live Shopify prices (needs SHOPIFY_STORE + SHOPIFY_ADMIN_TOKEN)
    python3 scripts/daily.py --snapshot-only      # skip the price refresh

Schedule it once a day (cron, a hosting provider's scheduler, etc.). Value history
in the app is built from these snapshots, so it starts on each card's intake date.
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from vault import db  # noqa: E402
from vault.pricing import get_source, item_value, sync_catalog  # noqa: E402


def snapshot(conn, day=None):
    day = day or db.today()
    items = conn.execute("SELECT * FROM vault_items WHERE status != 'shipped'").fetchall()
    for it in items:
        conn.execute(
            "INSERT OR REPLACE INTO value_snapshots (vault_item_id, day, value) VALUES (?, ?, ?)",
            (it["id"], day, item_value(conn, it)),
        )
    return len(items)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--snapshot-only", action="store_true")
    args = p.parse_args()
    db.init_db()
    with db.session() as conn:
        if not args.snapshot_only:
            cards, prices = sync_catalog(conn, get_source())
            print(f"Prices refreshed: {cards} cards, {prices} prices.")
        n = snapshot(conn)
        print(f"Saved today's value for {n} vault items.")


if __name__ == "__main__":
    main()
