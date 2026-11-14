# PriceFetchLTM — Project History & Data Quality Log

Reconstructed 2026-07-23 from git history (no Claude Code session transcripts were
indexed/available for this project — this doc is the substitute memory record).
Keep it updated going forward instead of relying on session recall.

## Build history (131 commits, 2026-01-22 → 2026-05-13)

**Phase 1 — Bootstrap (Jan 22–31, 27 commits)**
Initial scraper + GitHub Actions workflow → Supabase/PostgreSQL via `DATABASE_URL` →
secrets hardening (env vars, GitHub secrets for GSheet creds) → Playwright added →
master product loader + unified scraping pipeline → vintage/wine_color fields finalized.

**Phase 2 — Multi-retailer expansion + stabilization (Mar 10–11, 15 commits)**
Registry pattern added, multiple retailer scrapers brought online, idempotency-check
fixes (3 iterations to match the DB unique constraint), credentials blocked from git.

**Phase 3 — Reliability push (Mar 27–31, 19 commits)**
Twil bot-blocking diagnostics → chateaunet/wineclub/cavissima switched to Playwright →
parallel retailer scraping (performance) → `browser_utils` refactor → EUR currency
default fix → first `CLAUDE.md` handoff doc committed.

**Phase 4 — Coverage + discovery (Apr 8–14, 14 commits)**
New retailer scrapers (xovin, hallesdequercamps, e_leclerc, vin_malin,
vinotheque_bordeaux, cercledemartillac) → daily URL-discovery module + GitHub Actions
workflow → Jan–Mar backfill script → Apps Script dashboard chart → estate-name
standardization, cercledemartillac 403 fix.

**Phase 5 — Format integrity (Apr 29–30, 7 commits)**
Discovery expanded to all retailers → magnum/large-format URL filtering → 75cl-only
enforced pipeline-wide → blanc (white wine) tracking added.

**Phase 6 — Accuracy + reporting (May 1, 11 commits)**
Price detection/validation accuracy improvements, correction tracking, daily report
generation, ground-truth discount-date calibration.

**Phase 7 — Blanc rollout + QA infrastructure (May 4–5, 24 commits — biggest push)**
Full blanc coverage across millesima/millesimes/hallesdequercamps/vinatis/
chateauinternet → market analysis split by wine_color → case-size correction
(divisor-closest-to-median fix) → price review pipeline (`price_validator.py`) →
cavissima blanc audit tooling → user-feedback "flags" loop (`process_flags.py` +
GSheet `flags` tab) → daily_report tab consolidation.

**Phase 8 — Data hygiene (May 6–13, 4 commits)**
Estate-name self-healing on every loader run, NULL-vintage purge (455 stale
records), url_discovery GITHUB_TOKEN permissions fix, Twil case-price fix.

---

## Data quality audit — 2026-07-23

User-reported symptoms: Château Olivier 2017 (cavissima) prices "all over the
place"; most Château Carbonnieux references in May wrong; some cashvin prices
divided by 6 incorrectly, landing implausibly below €15; Château Sociando-Mallet
2022 (jean_merlaut) mixing bottle/magnum; cercledemartillac prices wrong,
especially 2025, showing case prices instead of bottle.

### Root cause 1 — soft-redirects not detected (cavissima, cashvin)

`cavissima.py`'s Playwright scraper only checks `response.status == 404`. When a
product is delisted, Shopify 301-redirects to `/collections/vin-bordeaux` (a
catalogue listing page) with **status 200** — undetected. `parse_tiles()` then
scrapes every `<label><small>` price tile on the *entire collection grid* and
picks whichever happens to have the smallest bottle count, i.e. an essentially
random product's price. This is why `master_product_id=65596` (Château Olivier
2017, cavissima) oscillated between €13 and €287 — confirmed live:
`GET /products/chateau-olivier-2017` → 301 → `/collections/vin-bordeaux`.

`cashvin.py` (WooCommerce, `GenericStaticScraper`) never set
`verify_url_match = True`, so the same class of bug applies whenever a cashvin
product redirects — the existing `verify_url_match` protection (already used by
vinotheque_bordeaux etc.) was simply never enabled for this retailer. This
explains the Carbonnieux 2015/2016/2018 (`ids 72536/72537/72538`) and 2019
(`id 12190`) swings between ~€5.95–14 and €169/€42 — identical wrong values
appeared across multiple different vintage URLs on the same dates, consistent
with a site-wide redirect (e.g. maintenance/home page) being scraped instead of
the product page.

**Fix:** `cavissima.py` — reject the page if the post-`goto()` URL no longer
contains `/products/`. `cashvin.py` — set `verify_url_match = True`.

### Root cause 2 — `content` microdata attribute is the pre-selected pack price, not the unit price (cercledemartillac)

cercledemartillac (PrestaShop) pages show the true per-bottle price as visible
text next to the price block — e.g. `"60,00€ la bouteille"` — but the
`content="360"` microdata attribute on the same element reflects whichever pack
option is pre-selected in the UI (often the wooden case of 6). The scraper's
selector logic (`el.get_attribute("content") or el.inner_text()`) prefers
`content`, so it silently records the **case total** as the bottle price
whenever a case format happens to be the default-selected variant.

Confirmed live:
- 2015/2016 (`ids 12610/12611`): raw scrape is always `"360"` (case of 6);
  true price is "60,00€ la bouteille". `price_validator.py`'s self-referencing
  median then flip-flops the correction between ÷6 (→60, correct) and ÷12
  (→30, wrong) depending on which bad value polluted the running median —
  explains the persistent 30↔60 oscillation.
- 2025 primeur (`id 84980`, no CSV row — auto-discovered, first-ever record so
  no median existed yet): `content="180"` sailed through unflagged because
  180 < `GLOBAL_MAX_EUR` (500). True price: "30,00€ la bouteille" (180/6=30 ✓).
- Vintages 2017–2023 and Blanc 2020–2023 are stable and correct — those product
  pages default to a single-bottle pack option, so `content` already equals
  the unit price. Only pages whose default pack is a case are affected.

**Fix:** parse the visible per-bottle text (`.product-prices` / the "la
bouteille" line) as the primary source; fall back to `content` only if that
text can't be found/parsed.

### Root cause 3 — CSV bottle/magnum URL history flip (jean_merlaut Sociando-Mallet 2022)

`master_product_id=320`'s `price_records.url` alternates historically between
`.../3629-chateau-sociando-mallet-2022.html` and `.../3630-...html`. Confirmed
via PrestaShop feature data:
- **3629** = `"Caisse bois 6 Bouteilles 75 cl"` → correct, standard bottle SKU.
- **3630** = `"Caisse bois 6 Magnums 150 cl"` → magnum SKU (€77.40/unit ≈ 2×
  bottle price).

`load_master_products.py` does `ON CONFLICT DO UPDATE` on `product_url` on
every load (this supersedes the "ON CONFLICT DO NOTHING" description in
`CLAUDE.md`, which is stale) — so whichever URL was in `master_products.csv`
on a given day got written straight into the single `master_products` row,
explaining why the same `master_product_id` shows both SKUs across time. The
CSV currently pins **3629 (bottle)**, which is correct. No code bug remains;
this was a historical CSV editing mistake. **Do not swap this URL back to
3630 — 3630 is the magnum.**

### Not yet investigated

`jean_merlaut` Château Sociando-Mallet 2022 was the only row checked in depth;
the same CSV-history-drift risk could in principle apply to any other product
where `product_url` was hand-edited more than once. No evidence found of this
elsewhere as of 2026-07-23, but the `price_records.url` column (stored per
scrape, not just on `master_products`) is the fastest way to check: group by
`master_product_id` and look for more than one distinct non-null `url`.

### GSheet reconciliation note

`export_to_gsheet.py`'s `price_records` tab is **append-only, keyed by
`price_records.id`** — it never re-syncs a row once appended. Fixing/deleting
rows in the DB does **not** retroactively update anything already exported to
GSheet. Any DB historical cleanup must be paired with an explicit GSheet-side
correction (delete/edit the corresponding rows in the sheet), not just a DB fix.

### Existing correction infrastructure (don't duplicate)

- `src/price_validator.py` — real-time case-price correction (÷6, ÷12) against
  a self-referencing historical median. Self-referencing means it can be
  poisoned by one bad value (see root causes above) and then perpetuate it.
- `src/process_flags.py` + GSheet `flags` tab — user-submitted correction
  workflow (`incorrect-url`, `wrong-format`, `404`, `validated-ok`). Applies
  DB fixes at the start of each run based on manually reviewed flags.
- Both predate this audit and were not sufficient to catch the bugs above
  because they React to symptoms (a bad price ratio) rather than the actual
  causes (soft-redirects, wrong microdata attribute, CSV drift).
