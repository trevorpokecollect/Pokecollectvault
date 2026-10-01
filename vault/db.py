"""SQLite storage for the vault proof of concept.

Kept deliberately thin (stdlib sqlite3, plain SQL) so it can move to Postgres
later with the same table layout.
"""
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone

SCHEMA = """
CREATE TABLE IF NOT EXISTS customers (
    id          INTEGER PRIMARY KEY,
    name        TEXT NOT NULL,
    email       TEXT UNIQUE,
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS catalog_cards (
    id            INTEGER PRIMARY KEY,
    source        TEXT NOT NULL,            -- e.g. 'shopify'
    source_id     TEXT NOT NULL,            -- id in that source
    name          TEXT NOT NULL,
    variant       TEXT,                     -- e.g. 'Alternate Art Secret'
    set_code      TEXT,
    set_name      TEXT,
    number        TEXT,
    language      TEXT NOT NULL,            -- 'English' | 'Japanese'
    rarity        TEXT,
    search_text   TEXT NOT NULL,
    UNIQUE (source, source_id)
);

CREATE TABLE IF NOT EXISTS catalog_prices (
    card_id     INTEGER NOT NULL REFERENCES catalog_cards(id),
    condition   TEXT NOT NULL,              -- NM | LP | MP | HP | DMG
    price       REAL NOT NULL,
    source      TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    PRIMARY KEY (card_id, condition)
);

CREATE TABLE IF NOT EXISTS vault_items (
    id            INTEGER PRIMARY KEY,
    vault_id      TEXT NOT NULL UNIQUE,     -- printed on the ID label, e.g. VLT-000001
    customer_id   INTEGER NOT NULL REFERENCES customers(id),
    card_id       INTEGER NOT NULL REFERENCES catalog_cards(id),
    kind          TEXT NOT NULL DEFAULT 'raw',   -- raw | graded
    condition     TEXT,                     -- for raw cards
    grader        TEXT,                     -- for graded cards
    grade         TEXT,
    cert_number   TEXT,
    status        TEXT NOT NULL DEFAULT 'in_vault',  -- in_vault | ship_requested | shipped
    front_image   TEXT,                     -- processed image path (relative to uploads)
    back_image    TEXT,
    front_thumb   TEXT,
    front_original TEXT,
    back_original TEXT,
    image_flags   TEXT,                     -- warnings from auto-crop, '' when clean
    intake_at     TEXT NOT NULL,
    notes         TEXT
);

CREATE TABLE IF NOT EXISTS value_snapshots (
    vault_item_id INTEGER NOT NULL REFERENCES vault_items(id),
    day           TEXT NOT NULL,            -- YYYY-MM-DD
    value         REAL,                     -- NULL when no price is available
    PRIMARY KEY (vault_item_id, day)
);

CREATE TABLE IF NOT EXISTS shipments (
    id            INTEGER PRIMARY KEY,
    vault_item_id INTEGER NOT NULL REFERENCES vault_items(id),
    customer_id   INTEGER NOT NULL REFERENCES customers(id),
    address       TEXT NOT NULL,
    status        TEXT NOT NULL DEFAULT 'requested',  -- requested | shipped | cancelled
    tracking      TEXT,
    requested_at  TEXT NOT NULL,
    shipped_at    TEXT
);

CREATE TABLE IF NOT EXISTS activity (
    id          INTEGER PRIMARY KEY,
    customer_id INTEGER NOT NULL REFERENCES customers(id),
    title       TEXT NOT NULL,
    detail      TEXT,
    status      TEXT,
    created_at  TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_items_customer ON vault_items(customer_id);
CREATE INDEX IF NOT EXISTS idx_cards_search ON catalog_cards(search_text);
"""

CONDITIONS = ["NM", "LP", "MP", "HP", "DMG"]
CONDITION_NAMES = {
    "NM": "Near Mint",
    "LP": "Lightly Played",
    "MP": "Moderately Played",
    "HP": "Heavily Played",
    "DMG": "Damaged",
}


def now_iso():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def today():
    return datetime.now(timezone.utc).date().isoformat()


def db_path():
    return os.environ.get("VAULT_DB", os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "vault.db"))


def connect(path=None):
    conn = sqlite3.connect(path or db_path())
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


@contextmanager
def session(path=None):
    conn = connect(path)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db(path=None):
    with session(path) as conn:
        conn.executescript(SCHEMA)


def next_vault_id(conn):
    row = conn.execute("SELECT MAX(id) AS m FROM vault_items").fetchone()
    n = (row["m"] or 0) + 1
    return f"VLT-{n:06d}"


def log_activity(conn, customer_id, title, detail="", status=""):
    conn.execute(
        "INSERT INTO activity (customer_id, title, detail, status, created_at) VALUES (?, ?, ?, ?, ?)",
        (customer_id, title, detail, status, now_iso()),
    )
