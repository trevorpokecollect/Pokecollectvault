"""Daily job: refresh prices from the price source, then save today's value for every vault item.

    python3 scripts/daily.py                      # uses PRICE_SOURCE (default: the seed file)
    PRICE_SOURCE=shopify python3 scripts/daily.py # live Shopify prices (needs SHOPIFY_STORE + SHOPIFY_ADMIN_TOKEN)
    python3 scripts/daily.py --snapshot-only      # skip the price refresh

On a hosted deployment the app runs this itself once a day (RUN_SCHEDULER=1), so
you only need this script for local runs or a manual refresh. Value history in
the app is built from these snapshots, so it starts on each card's intake date.
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from vault import jobs  # noqa: E402
from vault.jobs import snapshot  # noqa: E402,F401  (re-exported for tests and callers)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--snapshot-only", action="store_true")
    args = p.parse_args()
    jobs.ensure_ready()
    n = jobs.run_daily(refresh_prices=not args.snapshot_only)
    print(f"Saved today's value for {n} vault items.")


if __name__ == "__main__":
    main()
