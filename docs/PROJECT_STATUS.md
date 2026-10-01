# CloudBinder: project status

**Paused:** October 1, 2026. The concept works end to end, but the logistics below need to be settled before more is built.

CloudBinder, powered by Poke-Collect ("Your cards, vaulted."), is a card vault. Poke-Collect stores customers' cards, and customers track what their collection is worth in an app and can ask for any card to be shipped home.

## Where everything is

| What | Where |
| --- | --- |
| Live proof of concept | https://vault-app-production-6586.up.railway.app |
| Code | GitHub: `trevorpokecollect/Pokecollectvault`, branch `main` |
| Hosting | Railway project `5ca50c73-af95-4485-8468-8f4a679e1583`, service `vault-app`, volume `vault-data` mounted at `/data` |
| Secrets | Railway service variables (`STAFF_PASSCODE`, `SECRET_KEY`). A copy of the access details is in Trevor's Gmail drafts. Secrets never go in this repo. |
| Earlier planning | Launch plan in Claude Docs; clickable design prototype in Claude artifacts |

## What's built

- **Staff intake:** upload front and back photos or scans, automatic crop and straighten to a consistent card frame, catalog search, condition or grading details, assign to a customer, and a vault ID is issued.
- **Customer app (mobile):** total collection value with a 1M/3M/6M/1Y chart, per-card values, a 3D card viewer that tilts and flips, ship-home requests, and an activity feed.
- **Ship-outs:** staff queue, enter tracking, card marked shipped.
- **Prices:** from a snapshot of the Poke-Collect Shopify catalog (78 cards). A live Shopify pull is built but needs an Admin API token. A daily job saves value history.
- **Branding:** CloudBinder wordmark, Oswald type, Poke-Collect yellow on a deep purple theme.
- 15 automated tests pass.

## Open logistics to settle before building more

1. **Legal:** custody/bailment agreement, terms of service, and wording for displayed values. Keep the low-gambling-risk model, with no random packs or redemption odds (Alabama §13A-12-20).
2. **Insurance:** coverage for cards held in the vault and in transit, and declared values on shipping labels.
3. **Pricing data:** a licensed source for a separate business (TCGplayer/Storepass terms, JustTCG, or similar) plus graded-slab prices.
4. **Operations:** intake workflow and scanner hardware, storage and labeling system, audits, ship-out fees, and who handles what.
5. **Payments:** storage or ship-out fees, plus whether customers can buy into the vault.
6. **Business setup:** separate entity and inventory from the Poke-Collect store, and name clearance for "CloudBinder" (trademark attorney, domain, social handles).

## Technical work before real customers

- Real customer accounts and individual staff logins (the current sign-in is a demo picker and a shared passcode).
- Postgres instead of SQLite, and image storage in S3 or similar.
- CSRF protection, address validation, backups.
- Railway did not auto-deploy on push. Each deploy so far was started manually by reconnecting the repo; check the GitHub integration when work resumes.

## Picking it back up

See the README for running locally, tests, and deploy settings. The Railway service keeps running, and using resources, until it's paused or removed in the Railway dashboard.
