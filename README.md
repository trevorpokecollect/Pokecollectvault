# The Vault: proof of concept

A card vault for Pokémon collectors: staff take cards in with automatic image cropping, customers see their collection's value in a mobile app, and can request any card shipped home.

**Status:** proof of concept. Sign-in is a demo picker and a shared staff passcode, values come from a snapshot of the Poke-Collect Shopify catalog, and storage is local SQLite. See [Before real customers](#before-real-customers).

## What works

**Staff (desktop)**
- **Vault intake** (`/staff/intake`): upload or scan front and back, see the automatic crop instantly, search the catalog, record condition (or grader, grade and cert for slabs), assign an owner (or create one), and get a vault ID (`VLT-000001`).
- **Auto-crop** (`vault/imaging.py`): finds the card, straightens it, corrects perspective, crops to exact card proportions (630×880) or slab proportions (620×1024), and applies the same light contrast and sharpening to every card. It flags crops it isn't sure about ("Check crop") instead of silently saving a bad image. It works on scanner output and phone photos, and the originals are kept.
- **Ship-outs** (`/staff/shipments`): the queue of customer requests; enter a tracking number to mark one shipped.

**Customer (mobile)**
- **Collection** (`/app`): total value, change over 1M/3M/6M/1Y, a value chart, and every card with its own value and change.
- **Card detail**: an interactive 3D card (tilt with a moving glare, flip to the back), value history, condition, vault ID and status.
- **Ship home**: request a ship-out with an address; the card is marked and leaves the collection once staff ship it.
- **Activity**: intake, ship-out and shipped events.

## Run it

Needs Python 3.10+.

```bash
pip install -r requirements.txt
python3 scripts/seed.py      # creates data/vault.db, loads 78 catalog cards with prices, adds "Demo Collector"
python3 app.py               # http://127.0.0.1:5000
```

Staff passcode: `vault-demo` (set `STAFF_PASSCODE` to change it). Add a card through **Staff: vault intake**, then sign in as that customer on the home page.

Tests (15, including the full intake → collection → ship-out → shipped loop):

```bash
python3 -m unittest discover tests
```

## Deploy (Railway)

The repo includes `railway.json`, which starts the app with gunicorn and health-checks `/healthz`.

1. Create a service from this GitHub repo.
2. Add a **volume** mounted at `/data`, for the database and card images.
3. Set these variables:

   | Variable | Value |
   | --- | --- |
   | `RUN_SCHEDULER` | `1` (set up the database on start and run the daily price/value job inside the app) |
   | `VAULT_DB` | `/data/vault.db` |
   | `VAULT_UPLOADS` | `/data/uploads` |
   | `SECRET_KEY` | a long random string |
   | `STAFF_PASSCODE` | your staff passcode |

4. Generate a public domain.

On first start the app creates the database, loads the catalog, and adds the demo customer. The daily job runs once a day after `DAILY_HOUR_UTC` (default 9 UTC, about 3–4 a.m. Central). Keep a single app instance: the database is a file on the volume.

## Prices and value history

- `vault/pricing.py` defines a `PriceSource`. The app only reads the `catalog_prices` table, so swapping sources changes nothing else.
  - `seed` (default): `data/seed-catalog.json`, a snapshot of Poke-Collect's in-stock singles (Near Mint and other conditions).
  - `shopify`: live pull from the Shopify Admin API. Set `PRICE_SOURCE=shopify`, `SHOPIFY_STORE=poke-collect-al.myshopify.com` and `SHOPIFY_ADMIN_TOKEN` (a custom app token with `read_products`).
  - To add a licensed source later (JustTCG, PriceCharting for graded slabs, a Storepass/TCGplayer feed), write another `PriceSource` class.
- **Run `scripts/daily.py` once a day** when running locally (hosted deployments with `RUN_SCHEDULER=1` do this automatically). It refreshes prices and saves each card's value for the day. History and change figures come only from these real snapshots, so each card's chart starts on its intake date.
- Graded slabs are stored and shown but not priced yet (they show "—" until a graded price source is added).

## Configuration

| Variable | Default | Purpose |
| --- | --- | --- |
| `STAFF_PASSCODE` | `vault-demo` | Staff sign-in |
| `SECRET_KEY` | random per start | Session signing; set it so sign-ins survive restarts |
| `VAULT_DB` | `data/vault.db` | SQLite file |
| `VAULT_UPLOADS` | `uploads/` | Original and processed images |
| `PRICE_SOURCE` | `seed` | `seed` or `shopify` |
| `HOST`, `PORT` | `127.0.0.1`, `5000` | Dev server address |

## Layout

```
app.py               routes: customer app, staff intake, ship-outs, catalog search, crop preview
vault/db.py          schema (customers, catalog, prices, vault items, snapshots, shipments, activity)
vault/imaging.py     auto-crop pipeline (OpenCV)
vault/pricing.py     price sources, catalog sync, item valuation
scripts/seed.py      create DB, load catalog, demo customer
scripts/daily.py     daily price refresh + value snapshots
templates/, static/  server-rendered pages, 3D card and intake scripts
tests/               auto-crop and end-to-end tests (synthetic card images, no real artwork)
```

## Before real customers

- Real accounts and sign-in for customers and individual staff logins, CSRF protection on forms.
- Postgres instead of SQLite, and image storage in S3 or similar instead of local disk.
- A licensed price source (confirm terms for a separate business) plus graded slab prices.
- Ship-out fees, payments and address validation; insurance values on labels.
- Hosting with HTTPS and a production server (e.g. `gunicorn app:app`), plus the daily job on a scheduler.
- Legal review: custody agreement, terms, and value-display wording.
