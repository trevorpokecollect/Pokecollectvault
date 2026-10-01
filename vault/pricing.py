"""Catalog parsing and price sources.

A price source returns catalog rows (card identity + per-condition prices).
Swap sources without touching the rest of the app: the vault only ever reads
the catalog_prices table.
"""
import json
import os
import re
import urllib.request

from .db import CONDITIONS, now_iso

CONDITION_FROM_VARIANT = {
    "near mint": "NM",
    "lightly played": "LP",
    "moderately played": "MP",
    "heavily played": "HP",
    "damaged": "DMG",
}


def parse_title(title):
    """Split a store title into (name, variant).

    'Rayquaza VMAX (Alternate Art Secret) 218/203 - SWSH07 ...' -> ('Rayquaza VMAX', 'Alternate Art Secret')
    'Mew ex - 347/190 - SV4a Shiny Treasure ex Holofoil'          -> ('Mew ex', None)
    'Bulbasaur 143 - SV07 Stellar Crown Holofoil'                -> ('Bulbasaur', None)
    """
    head = title.split(" - ")[0].strip()
    variant = None
    m = re.search(r"\(([^)]+)\)", head)
    if m:
        variant = m.group(1).strip()
        head = (head[: m.start()] + head[m.end():]).strip()
    tokens = head.split()
    while tokens and re.search(r"\d", tokens[-1]):
        tokens.pop()
    return " ".join(tokens) or head, variant


def parse_set(vendor):
    """'SWSH07: Evolving Skies' -> ('SWSH07', 'Evolving Skies'); 'SM Promos' -> (None, 'SM Promos')."""
    if ":" in vendor:
        code, name = vendor.split(":", 1)
        return code.strip(), name.strip()
    return None, vendor.strip()


def card_row(source, source_id, title, vendor, language, rarity, number):
    name, variant = parse_title(title)
    set_code, set_name = parse_set(vendor)
    search = " ".join(filter(None, [name, variant, set_code, set_name, number, language, rarity])).lower()
    return {
        "source": source,
        "source_id": str(source_id),
        "name": name,
        "variant": variant,
        "set_code": set_code,
        "set_name": set_name,
        "number": number,
        "language": language,
        "rarity": rarity,
        "search_text": search,
    }


class PriceSource:
    """Interface: name + fetch() -> list of (card_row, {condition: price})."""

    name = "base"

    def fetch(self):
        raise NotImplementedError


class SeedFileSource(PriceSource):
    """Snapshot of the Poke-Collect Shopify catalog saved in data/seed-catalog.json."""

    name = "shopify"

    def __init__(self, path=None):
        self.path = path or os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "seed-catalog.json")

    def fetch(self):
        with open(self.path, encoding="utf-8") as f:
            rows = json.load(f)
        out = []
        for source_id, title, vendor, lang, rarity, number, prices in rows:
            language = "Japanese" if lang == "JP" else "English"
            card = card_row("shopify", source_id, title, vendor, language, rarity, number)
            out.append((card, {k: v for k, v in prices.items() if v and v > 0}))
        return out


class ShopifyAdminSource(PriceSource):
    """Live pull from the Shopify Admin GraphQL API.

    Needs SHOPIFY_STORE (e.g. poke-collect-al.myshopify.com) and SHOPIFY_ADMIN_TOKEN
    (a custom app token with read_products). Reads Pokémon singles and their
    condition variants; $0 variants are skipped.
    """

    name = "shopify"
    QUERY = """
    query($q: String!, $after: String) {
      products(first: 100, query: $q, after: $after) {
        nodes { id title vendor productType tags variants(first: 6) { nodes { title price } } }
        pageInfo { hasNextPage endCursor }
      }
    }"""

    def __init__(self, store=None, token=None, query="tag:Type_Single AND tag:Brand_Pokemon", api_version="2025-07"):
        self.store = store or os.environ.get("SHOPIFY_STORE")
        self.token = token or os.environ.get("SHOPIFY_ADMIN_TOKEN")
        self.query = query
        self.api_version = api_version
        if not self.store or not self.token:
            raise RuntimeError("Set SHOPIFY_STORE and SHOPIFY_ADMIN_TOKEN to sync live Shopify prices.")

    def _post(self, variables):
        req = urllib.request.Request(
            f"https://{self.store}/admin/api/{self.api_version}/graphql.json",
            data=json.dumps({"query": self.QUERY, "variables": variables}).encode(),
            headers={"Content-Type": "application/json", "X-Shopify-Access-Token": self.token},
        )
        with urllib.request.urlopen(req, timeout=60) as resp:
            return json.load(resp)

    def fetch(self):
        out, after = [], None
        while True:
            data = self._post({"q": self.query, "after": after})["data"]["products"]
            for p in data["nodes"]:
                tags = p["tags"]
                tag = lambda prefix: next((t.split("_", 1)[1] for t in tags if t.startswith(prefix + "_")), None)
                language = "Japanese" if "Japanese" in (p["productType"] or "") else "English"
                card = card_row("shopify", p["id"].rsplit("/", 1)[-1], p["title"], p["vendor"], language, tag("Rarity"), tag("Number"))
                prices = {}
                for v in p["variants"]["nodes"]:
                    cond = CONDITION_FROM_VARIANT.get((v["title"] or "").lower())
                    price = float(v["price"] or 0)
                    if cond and price > 0:
                        prices[cond] = price
                out.append((card, prices))
            if not data["pageInfo"]["hasNextPage"]:
                return out
            after = data["pageInfo"]["endCursor"]


def get_source(name=None):
    name = name or os.environ.get("PRICE_SOURCE", "seed")
    if name == "seed":
        return SeedFileSource()
    if name == "shopify":
        return ShopifyAdminSource()
    raise ValueError(f"Unknown price source '{name}'. Use 'seed' or 'shopify'.")


def sync_catalog(conn, source):
    """Upsert catalog cards and their prices from a source. Returns (cards, prices) counts."""
    stamp = now_iso()
    cards = prices = 0
    for card, cond_prices in source.fetch():
        conn.execute(
            """INSERT INTO catalog_cards (source, source_id, name, variant, set_code, set_name, number, language, rarity, search_text)
               VALUES (:source, :source_id, :name, :variant, :set_code, :set_name, :number, :language, :rarity, :search_text)
               ON CONFLICT (source, source_id) DO UPDATE SET
                 name = excluded.name, variant = excluded.variant, set_code = excluded.set_code,
                 set_name = excluded.set_name, number = excluded.number, language = excluded.language,
                 rarity = excluded.rarity, search_text = excluded.search_text""",
            card,
        )
        card_id = conn.execute(
            "SELECT id FROM catalog_cards WHERE source = ? AND source_id = ?", (card["source"], card["source_id"])
        ).fetchone()["id"]
        cards += 1
        for cond, price in cond_prices.items():
            if cond not in CONDITIONS:
                continue
            conn.execute(
                """INSERT INTO catalog_prices (card_id, condition, price, source, updated_at) VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT (card_id, condition) DO UPDATE SET price = excluded.price, source = excluded.source,
                   updated_at = excluded.updated_at""",
                (card_id, cond, price, source.name, stamp),
            )
            prices += 1
    return cards, prices


def item_value(conn, item):
    """Current estimated value of a vault item, or None if it can't be priced yet."""
    if item["kind"] != "raw" or not item["condition"]:
        return None  # graded slabs need a graded price feed (e.g. PriceCharting) — not wired up yet
    row = conn.execute(
        "SELECT price FROM catalog_prices WHERE card_id = ? AND condition = ?", (item["card_id"], item["condition"])
    ).fetchone()
    return row["price"] if row else None
