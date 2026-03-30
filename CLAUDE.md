# Wine Price Scraper — Project Handoff

## Project Overview
Automated daily wine price monitoring system for French Bordeaux retailers.
Scrapes ~300 product URLs across 15 retailers, stores prices in PostgreSQL (Supabase),
exports to Google Sheets, and sends weekly alert emails via Apps Script.

## Repository
GitHub: https://github.com/flemingsk/wine-price-scraper
Branch: main
CI/CD: GitHub Actions — runs daily + on every push

## Tech Stack
- Python 3.13
- SQLAlchemy + psycopg2 (PostgreSQL via Supabase)
- requests + BeautifulSoup (static scrapers)
- Playwright (JS-rendered sites)
- gspread (Google Sheets export)
- GitHub Actions (scheduling)

## Architecture
```
master_products.csv
    → load_master_products.py  (upsert to DB)
    → app.py                   (orchestrator)
        → scraper_engine.py    (parallel retailer dispatch)
            → scrapers/        (one file per retailer)
        → export_to_gsheet.py  (write to Google Sheet)
```

## File Structure
```
src/
├── app.py                    # Entry point, parallel worker orchestration
├── scraper_engine.py         # Per-retailer threading, DB persistence
├── db.py                     # SQLAlchemy engine + SessionLocal
├── models.py                 # MasterProduct, PriceRecord ORM models
├── utils.py                  # parse_price() — handles EU/UK formats, defaults EUR
├── load_master_products.py   # CSV → DB upsert (ON CONFLICT DO NOTHING)
├── export_to_gsheet.py       # Appends new price_records rows to GSheet
└── scrapers/
    ├── base.py               # BaseScraper interface + ScrapeResult dataclass
    ├── generic.py            # GenericStaticScraper — base for simple retailers
    ├── browser_utils.py      # REQUESTS_HEADERS, polite_delay, get_playwright_context
    ├── registry.py           # get_scraper(retailer) dispatcher
    ├── __init__.py           # SCRAPERS dict
    ├── millesima.py          # Custom: tile-based format/price parsing (static)
    ├── vinatis.py            # Playwright
    ├── idealwine.py          # Playwright
    ├── twil.py               # Playwright — unit price: span.price
    ├── wineandco.py          # Playwright
    ├── wineclub.py           # Playwright — selector: span.normal-price
    ├── cavissima.py          # Playwright + tile logic (label > small)
    ├── chateaunet.py         # Playwright — selector: span[itemprop='price']
    ├── jean_merlaut.py       # GenericStaticScraper
    ├── twelvebouteilles.py   # GenericStaticScraper
    ├── lavignery.py          # GenericStaticScraper (+ VinodisScraper subclass)
    ├── aries.py              # GenericStaticScraper
    ├── dubecq.py             # GenericStaticScraper
    └── wine_searcher.py      # API-based (optional, requires WINE_SEARCHER_API_KEY)
master_products.csv           # Source of truth for products to scrape
```

## Database (Supabase PostgreSQL)
**Tables:**
- `master_products` — one row per estate+retailer+vintage combination
  - Key columns: estate_name, retailer, product_url, url_template, price_selector,
    vintage_start, vintage_end, bottle_size, active, wine_color, notes
  - Unique constraint: `uq_master_product` (retailer, estate_name, vintage_start, bottle_size)
  - price_selector and availability_selector are NULLABLE (run ALTER if constraint exists)

- `price_records` — one row per scrape result
  - Key columns: master_product_id, site, vintage, price_amount, currency,
    raw_price_text, availability, fetched_at, wine_color, url
  - Deduplication: one record per (master_product_id, vintage, DATE(fetched_at))

**Important SQL fixes to run if needed:**
```sql
-- Drop NOT NULL constraints that block scrapers without CSS selectors
ALTER TABLE master_products ALTER COLUMN price_selector DROP NOT NULL;
ALTER TABLE master_products ALTER COLUMN availability_selector DROP NOT NULL;

-- Fix UNKNOWN currency entries
UPDATE price_records SET currency = 'EUR' WHERE currency = 'UNKNOWN';

-- Clear today's null Twil records (if Twil saved nulls before fix)
DELETE FROM price_records
WHERE site = 'twil' AND price_amount IS NULL
AND DATE(fetched_at) = CURRENT_DATE;
```

## GitHub Secrets Required
| Secret | Purpose |
|--------|---------|
| `DATABASE_URL` | Supabase PostgreSQL connection string |
| `GSHEET_CREDENTIALS_JSON` | Google service account JSON (raw, no surrounding quotes) |
| `WINE_SEARCHER_API_KEY` | Optional — Wine-Searcher API |

## Scraper Architecture Rules
- All scrapers inherit from `BaseScraper` and return `list[ScrapeResult]`
- Simple static HTML retailers → inherit `GenericStaticScraper` (3-line file)
- JS-rendered retailers → inherit `BaseScraper`, use Playwright
- Millesima + Cavissima → custom tile parsing logic (case format detection)
- Never raise exceptions from scrape() — catch internally, return []
- `polite_delay(0.5, 1.5)` for static, `polite_delay(1.0, 2.0)` for Playwright

## Retailer Registry
| Retailer key | Scraper class | Type |
|---|---|---|
| millesima | MillesimaScraper | Static + tile logic |
| vinatis | VinatisScraper | Playwright |
| idealwine | IdealwineScraper | Playwright |
| twil | TwilScraper | Playwright |
| wineandco | WineandcoScraper | Playwright |
| wineclub | WineclubScraper | Playwright |
| cavissima | CavissimaScraper | Playwright + tile logic |
| chateaunet | ChateaunetScraper | Playwright |
| jean_merlaut | JeanMerlautScraper | Generic static |
| 12bouteilles | TwelveBouteillesScraper | Generic static |
| lavignery | LaVigneryScraper | Generic static |
| vinodis | VinodisScraper | Generic static |
| aries | AriesScraper | Generic static |
| wineclub | WineclubScraper | Playwright |
| dubecq | DubecqScraper | Generic static |
| wine-searcher | WineSearcherScraper | API |

## Performance (app.py)
- MAX_WORKERS = 4 (parallel retailer threads)
- To revert to sequential: set MAX_WORKERS = 1
- Each thread gets its own DB session via SessionLocal()
- Retailers scrape their own products sequentially within each thread

## Known Issues / Open Items
1. **Twil prices**: previously scraped case totals (span#totalPrice) — now fixed to unit price (span.price). Delete any null price_amount Twil records from DB.
2. **master_products.csv must be committed** to repo for changes to take effect in GitHub Actions
3. **Cavissima/Wineclub URLs**: some may need fragment (#/...) stripped
4. **Chateaunet URLs**: contain tracking params (?_gl=...) — scraper strips these automatically at runtime
5. **Data gap Jan–March**: missing daily entries due to early bugs. Backfill SQL pending.
6. **Looker Studio**: currently connected to GSheet — consider switching to Supabase direct PostgreSQL connection for live data

## Google Sheets / Alerts
- Weekly alert script: `WinePriceAlerts.gs` in Google Apps Script (attached to the GSheet)
- Alerts: price outliers (±20% from mean), weekly changes (>5%), delistings
- Runs every Monday 8am via Apps Script trigger
- Output tabs: `alerts` (full history), `alert_latest` (current week)

## Adding a New Retailer
1. Add rows to `master_products.csv` with correct `retailer`, `product_url`, `price_selector`
2. Create `src/scrapers/{retailer}.py`:
   - Simple static site: 3 lines inheriting `GenericStaticScraper`
   - JS-rendered: copy `wineandco.py` pattern
3. Add to `src/scrapers/registry.py` and `src/scrapers/__init__.py`
4. Commit and push CSV + new scraper files

## Running Locally
```bash
cd PriceFetchLTM
python -m src.app
```

## Deployment
Push to main → GitHub Actions triggers automatically.
Manual trigger: GitHub → Actions → Run workflow.
