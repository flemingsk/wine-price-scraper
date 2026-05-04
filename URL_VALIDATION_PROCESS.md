# URL & Attribution Validation Process

## The two types of data errors

| Type | What went wrong | Action on price_records |
|---|---|---|
| **Bad URL** | URL is for a different product (wrong estate, magnum, redirect) | Delete — prices are wrong |
| **Wrong attribution** | URL/price is correct but the estate_name or vintage in master_products is wrong | Correct — reassign master_product_id or update URL/vintage |

Never delete records just because a URL changed to an equivalent one.  
Never keep records that contain prices for the wrong product.

---

## When a URL error is found (bad URL)

1. Set `active=FALSE` in `master_products.csv` with a note explaining why.
2. Commit with `[skip ci]` to avoid triggering a new scrape.
3. Run the appropriate cleanup workflow (`deactivate_invalid_entry` or a purpose-built script) with `dry_run=true` first, then `false`.
4. The cleanup script must: set `active=FALSE` in DB, delete `price_records`, remove GSheet rows.
5. **Do not just delete the row from the CSV.** A deleted CSV row leaves the DB record `active=TRUE`; the scraper keeps hitting it and `url_discovery` (before fix 845afee) would re-add it.

## When an attribution error is found (wrong estate / URL swap)

1. Fix `estate_name`, `vintage_start`/`vintage_end`, or `product_url` in `master_products.csv`.
2. Commit with `[skip ci]`.
3. Wait for the next full scraper run so `load_master_products` creates/updates the correct master_product rows.
4. Run `correct_db_attribution` workflow (or a purpose-built script) to:
   - Reassign `price_records.master_product_id` from the old (wrong) row to the new (correct) row.
   - For URL-only changes: update `price_records.url` to the new URL.
   - For vintage range errors: delete records with the wrong vintage (no alternative — changing vintage creates duplicates).
   - Deactivate orphaned master_product rows.

## Vintage range entries (vintage_start ≠ vintage_end)

Twil and similar scrapers use the same URL for a range of vintages. This only makes sense when the URL contains a `url_template` with `{vintage}`. **If there is no url_template, the vintage range must equal exactly one vintage.** Setting `vintage_end > vintage_start` on a static URL causes every vintage in the range to store the same price under a different vintage label.

**Rule:** For any retailer without a `url_template`, set `vintage_start == vintage_end`. Use one CSV row per URL/variant.

---

## Pre-addition URL validation checklist

Before adding any new row to `master_products.csv`:

### 1. URL resolves to the right product
- Open the URL in a browser (or `curl -L`) and confirm:
  - Page title / product name matches the estate name in the CSV.
  - Vintage shown on page matches `vintage_start`.
  - Bottle format shown is 75cl (not magnum, half-bottle, case).
- For Shopify / Twil fragment URLs: the fragment ID is variant-specific — confirm the variant shown on the product page matches the intended vintage and size.

### 2. URL does not redirect to a different product
- Watch for Shopify 302 redirects (common on cavissima): add `?variant=XXXXX` to anchor to the specific variant.
- For wineandco: the numeric suffix (e.g. `/27995`) is the product ID — different IDs for the same slug usually mean different bottle sizes.
- For vinotheque-bordeaux: the leading numeric ID (e.g. `2744-62400`) is the product listing ID — verify it still resolves to a product page, not a category.

### 3. Estate name in CSV matches the URL slug
- For chateaunet: the URL slug contains the estate name (e.g. `chateau-malartic-lagraviere-XXXXXXX`) — the `estate_name` column must match this, not a neighbouring estate.
- Cross-check: if the URL slug mentions "malartic" and the CSV row says "Larrivet Haut-Brion", that is an attribution error.

### 4. No duplicate coverage
- Search the CSV for the same estate + retailer + vintage combination before adding.
- Check that the new URL is not already tracked under a different row.

---

## Proposed automation: URL pre-commit validation script

`src/validate_csv_urls.py` — to be run locally before committing CSV changes, or as a CI check on PRs that touch `master_products.csv`.

What it would check:

```
For each new or changed row in master_products.csv:
  a. HTTP HEAD request to confirm URL resolves (no 404, no wrong-domain redirect)
  b. Extract page title via GET and warn if estate name not found in title
  c. Flag if vintage_end > vintage_start AND url_template is empty
  d. Flag duplicate (retailer, estate_name, vintage_start, bottle_size) keys
  e. Flag suspicious bottle_size mismatches (URLs containing "magnum", "150cl", "1.5L")
```

**Limitations:**
- JS-rendered pages (Playwright retailers) need headless browsing to get the real title — static HEAD/GET is sufficient only for static retailers.
- Shopify redirect detection requires following the redirect chain and checking the final URL slug.
- False positives are possible (estate names don't always appear verbatim in page titles).

**Suggested implementation priority:**
1. Duplicate key detection (pure CSV, zero network calls) — implement first.
2. Vintage range without url_template check (pure CSV) — implement alongside (1).
3. HTTP 404 / redirect detection for static retailers — useful, low cost.
4. Page title matching — best-effort, flag only, do not block.
