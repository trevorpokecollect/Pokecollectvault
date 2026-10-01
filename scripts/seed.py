"""Create the database, load the catalog and prices, and add a demo customer.

    python3 scripts/seed.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from vault import db  # noqa: E402
from vault.pricing import get_source, sync_catalog  # noqa: E402


def main():
    db.init_db()
    with db.session() as conn:
        cards, prices = sync_catalog(conn, get_source())
        if not conn.execute("SELECT 1 FROM customers LIMIT 1").fetchone():
            conn.execute(
                "INSERT INTO customers (name, email, created_at) VALUES (?, ?, ?)",
                ("Demo Collector", "demo@example.com", db.now_iso()),
            )
    print(f"Database ready at {db.db_path()}: {cards} catalog cards, {prices} prices.")


if __name__ == "__main__":
    main()
