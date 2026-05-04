# src/cleanup_gsheet_invalid_records.py
"""
One-off: remove stale rows from the GSheet 'price_records' tab that
correspond to the 10 invalid cavissima Blanc master_products now deleted
from the DB (rouge-URL and 404-URL entries).

Also re-runs the cavissima_blanc_audit to reflect the clean DB state.

Run via GitHub Actions: cleanup_gsheet_invalid_records workflow.
"""
from __future__ import annotations

import logging
import os

from src.export_to_gsheet import _get_gsheet_client, GOOGLE_SHEET_NAME
from src.audit_cavissima_blanc import run_audit, export_audit_to_gsheet

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

PRICE_RECORDS_TAB = "price_records"

INVALID_URLS = {
    # Original batch: rouge URLs mislabelled as Blanc, and 404 Carbonnieux pages
    "https://www.cavissima.com/products/chateau-de-fieuzal-2015",
    "https://www.cavissima.com/products/chateau-malartic-lagraviere-2025",
    "https://www.cavissima.com/products/chateau-carbonnieux-blanc-2015",
    "https://www.cavissima.com/products/chateau-carbonnieux-blanc-2016",
    "https://www.cavissima.com/products/chateau-carbonnieux-blanc-2017",
    "https://www.cavissima.com/products/chateau-carbonnieux-blanc-2018",
    "https://www.cavissima.com/products/chateau-carbonnieux-blanc-2020",
    "https://www.cavissima.com/products/chateau-carbonnieux-blanc-2021",
    "https://www.cavissima.com/products/chateau-carbonnieux-blanc-2022",
    "https://www.cavissima.com/products/chateau-carbonnieux-blanc-2023",
    # Fieuzal Blanc 2020–2022: blanc product URL (may appear as-is in GSheet Link column)
    "https://www.cavissima.com/products/chateau-de-fieuzal-blanc-2020",
    "https://www.cavissima.com/products/chateau-de-fieuzal-blanc-2021",
    "https://www.cavissima.com/products/chateau-de-fieuzal-blanc-2022",
    # Fieuzal: rouge redirect URLs the scraper may have stored after Shopify redirect
    "https://www.cavissima.com/products/chateau-de-fieuzal-2020",
    "https://www.cavissima.com/products/chateau-de-fieuzal-2020?_pos=3&_fid=5018eb138&_ss=c",
    "https://www.cavissima.com/products/chateau-de-fieuzal-2021",
    "https://www.cavissima.com/products/chateau-de-fieuzal-2021?_pos=2&_fid=5018eb138&_ss=c",
    "https://www.cavissima.com/products/chateau-de-fieuzal-2022",
    "https://www.cavissima.com/products/chateau-de-fieuzal-2022?_pos=4&_fid=5018eb138&_ss=c",
    # Olivier Blanc 2018–2022: blanc product URL (may appear as-is in GSheet Link column)
    "https://www.cavissima.com/products/chateau-olivier-blanc-2018",
    "https://www.cavissima.com/products/chateau-olivier-blanc-2019",
    "https://www.cavissima.com/products/chateau-olivier-blanc-2020",
    "https://www.cavissima.com/products/chateau-olivier-blanc-2021",
    "https://www.cavissima.com/products/chateau-olivier-blanc-2022",
    # Olivier: rouge redirect URLs the scraper may have stored after Shopify redirect
    "https://www.cavissima.com/products/chateau-olivier-2018",
    "https://www.cavissima.com/products/chateau-olivier-2019",
    "https://www.cavissima.com/products/chateau-olivier-2020",
    "https://www.cavissima.com/products/chateau-olivier-2021",
    "https://www.cavissima.com/products/chateau-olivier-2021?_pos=2&_fid=e6ec2800e&_ss=c",
    "https://www.cavissima.com/products/chateau-olivier-2022",
    "https://www.cavissima.com/products/chateau-olivier-2022?_pos=1&_fid=e6ec2800e&_ss=c&variant=50452284408155",
    "https://www.cavissima.com/products/chateau-olivier-2022?variant=50452284408155",
    # Fieuzal Blanc 2019/2023 and Olivier Blanc 2023 — all 404 pages
    "https://www.cavissima.com/products/chateau-de-fieuzal-blanc-2019",
    "https://www.cavissima.com/products/chateau-de-fieuzal-blanc-2023",
    "https://www.cavissima.com/products/chateau-olivier-blanc-2023",
}

DRY_RUN = os.getenv("DRY_RUN", "").lower() in ("1", "true", "yes")


def clean_price_records_tab() -> int:
    """Remove rows matching invalid URLs from the price_records GSheet tab.
    Returns count of rows removed.
    """
    client = _get_gsheet_client()
    sheet  = client.open(GOOGLE_SHEET_NAME)
    ws     = sheet.worksheet(PRICE_RECORDS_TAB)

    print("Reading price_records tab...", flush=True)
    all_rows = ws.get_all_values()

    if not all_rows:
        print("price_records tab is empty — nothing to do.", flush=True)
        return 0

    header = all_rows[0]
    # GSheet header uses 'Link' as the URL column name
    url_col = None
    for candidate in ("Link", "url", "URL", "link"):
        if candidate in header:
            url_col = header.index(candidate)
            break
    if url_col is None:
        raise RuntimeError(f"URL column not found in price_records header: {header}")

    # Find row indices (1-based, skipping header) of bad rows
    bad_row_indices = []
    for i, row in enumerate(all_rows[1:], start=2):
        cell_url = row[url_col].strip() if url_col < len(row) else ""
        if cell_url in INVALID_URLS:
            bad_row_indices.append(i)

    print(f"Found {len(bad_row_indices)} stale rows to remove.", flush=True)

    if not bad_row_indices:
        print("No stale rows found — GSheet already clean.", flush=True)
        return 0

    if DRY_RUN:
        print(f"DRY RUN — would delete {len(bad_row_indices)} rows. No changes made.", flush=True)
        return len(bad_row_indices)

    # Delete from bottom to top so row indices stay valid
    for row_idx in sorted(bad_row_indices, reverse=True):
        ws.delete_rows(row_idx)

    print(f"Deleted {len(bad_row_indices)} stale rows from price_records tab.", flush=True)
    return len(bad_row_indices)


def main() -> None:
    print(f"=== cleanup_gsheet_invalid_records starting (dry_run={DRY_RUN}) ===", flush=True)

    print("\nStep 1: Cleaning stale rows from price_records GSheet tab...", flush=True)
    removed = clean_price_records_tab()

    print("\nStep 2: Re-running cavissima_blanc_audit to reflect clean DB state...", flush=True)
    results = run_audit(dry_run=True)  # DB already corrected; dry_run prevents double-correction
    export_audit_to_gsheet(results)

    print(f"\n=== Done. Removed {removed} stale GSheet rows; audit tab refreshed. ===", flush=True)


if __name__ == "__main__":
    main()
