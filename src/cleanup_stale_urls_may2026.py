# src/cleanup_stale_urls_may2026.py
"""
One-off: deactivate 3 stale product URLs found in the daily report (2026-05-04).

Stale entries:
  - vinatis Latour-Martillac Blanc 2016: URL redirects to 2nd wine 2023 (Lagrave-Martillac)
  - vinotheque Fieuzal Rouge 2015: URL is not a product page
  - vinotheque Latour-Martillac Rouge 2016: URL is not a product page

CSV already updated to active=FALSE. This script:
  1. Sets active=FALSE in the DB (propagated from CSV by load_master_products on next run,
     but applied here immediately so the scraper won't touch them today)
  2. Deletes all price_records for these master_products
  3. Removes matching rows from the GSheet price_records tab
"""
from __future__ import annotations

import logging
import os

from sqlalchemy import text

from .db import engine
from .export_to_gsheet import _get_gsheet_client, GOOGLE_SHEET_NAME

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

STALE_URLS = [
    "https://www.vinatis.com/55537-chateau-latour-martillac-blanc-2016",
    "https://vinotheque-bordeaux.com/bordeaux/4757-62503-chateau-de-fieuzal.html",
    "https://vinotheque-bordeaux.com/bordeaux/2744-62400-chateau-latour-martillac.html",
]

PRICE_RECORDS_TAB = "price_records"
DRY_RUN = os.getenv("DRY_RUN", "").lower() in ("1", "true", "yes")


def clean_db() -> tuple[int, int]:
    """Deactivate master_products and delete price_records for stale URLs.
    Returns (products_deactivated, records_deleted).
    """
    deactivated = 0
    deleted = 0

    with engine.begin() as conn:
        for url in STALE_URLS:
            row = conn.execute(
                text("SELECT id, estate_name, retailer FROM master_products WHERE product_url = :url"),
                {"url": url},
            ).fetchone()

            if not row:
                logger.warning(f"No master_product found for URL: {url}")
                continue

            logger.info(f"Found: id={row.id} | {row.estate_name} @ {row.retailer}")

            if DRY_RUN:
                n = conn.execute(
                    text("SELECT COUNT(*) FROM price_records WHERE master_product_id = :id"),
                    {"id": row.id},
                ).scalar()
                logger.info(f"  DRY RUN — would deactivate + delete {n} price_records")
                deactivated += 1
                deleted += n
                continue

            conn.execute(
                text("UPDATE master_products SET active = FALSE WHERE id = :id"),
                {"id": row.id},
            )
            n = conn.execute(
                text("DELETE FROM price_records WHERE master_product_id = :id"),
                {"id": row.id},
            ).rowcount
            logger.info(f"  Deactivated master_product, deleted {n} price_records")
            deactivated += 1
            deleted += n

    return deactivated, deleted


def clean_gsheet() -> int:
    """Remove rows matching stale URLs from the GSheet price_records tab.
    Returns count of rows removed.
    """
    stale_set = set(STALE_URLS)

    client = _get_gsheet_client()
    sheet  = client.open(GOOGLE_SHEET_NAME)
    ws     = sheet.worksheet(PRICE_RECORDS_TAB)

    print("Reading price_records tab...", flush=True)
    all_rows = ws.get_all_values()

    if not all_rows:
        print("price_records tab is empty — nothing to do.", flush=True)
        return 0

    header = all_rows[0]
    url_col = None
    for candidate in ("Link", "url", "URL", "link"):
        if candidate in header:
            url_col = header.index(candidate)
            break
    if url_col is None:
        raise RuntimeError(f"URL column not found in price_records header: {header}")

    bad_row_indices = []
    for i, row in enumerate(all_rows[1:], start=2):
        cell_url = row[url_col].strip() if url_col < len(row) else ""
        if cell_url in stale_set:
            bad_row_indices.append(i)

    print(f"Found {len(bad_row_indices)} stale GSheet rows to remove.", flush=True)

    if not bad_row_indices:
        return 0

    if DRY_RUN:
        print(f"DRY RUN — would delete {len(bad_row_indices)} GSheet rows.", flush=True)
        return len(bad_row_indices)

    for row_idx in sorted(bad_row_indices, reverse=True):
        ws.delete_rows(row_idx)

    print(f"Deleted {len(bad_row_indices)} stale rows from price_records tab.", flush=True)
    return len(bad_row_indices)


def main() -> None:
    print(f"=== cleanup_stale_urls_may2026 starting (dry_run={DRY_RUN}) ===", flush=True)

    print("\nStep 1: DB — deactivate master_products + delete price_records...", flush=True)
    deactivated, deleted = clean_db()
    print(f"  {deactivated} master_products deactivated, {deleted} price_records deleted.", flush=True)

    print("\nStep 2: GSheet — remove stale rows from price_records tab...", flush=True)
    removed = clean_gsheet()

    print(f"\n=== Done. DB: {deactivated} deactivated, {deleted} records deleted. "
          f"GSheet: {removed} rows removed. ===", flush=True)


if __name__ == "__main__":
    main()
