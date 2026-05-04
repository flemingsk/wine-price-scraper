# src/deactivate_invalid_entry.py
"""
One-off: deactivate a specific master_product by URL and delete its
historical price_records from both the DB and GSheet.

Set INVALID_URL env var to the product URL to remove, or edit the
INVALID_URL constant below.

Run via GitHub Actions: deactivate_invalid_entry workflow.
"""
from __future__ import annotations

import logging
import os

from sqlalchemy import text

from src.db import engine
from src.export_to_gsheet import _get_gsheet_client, GOOGLE_SHEET_NAME

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

# URL of the master_product to deactivate — override with INVALID_URL env var
INVALID_URL = os.getenv(
    "INVALID_URL",
    "https://www.vintageandco.com/46961.chateau-carbonnieux-2018-pessac-leognan-bordeaux.html",
)

DRY_RUN = os.getenv("DRY_RUN", "").lower() in ("1", "true", "yes")

PRICE_RECORDS_TAB = "price_records"


def clean_db() -> tuple[int, int]:
    """Deactivate master_product and delete its price_records.
    Returns (master_products_updated, price_records_deleted).
    """
    with engine.begin() as conn:
        row = conn.execute(
            text("SELECT id, estate_name, retailer, vintage_start, bottle_size FROM master_products WHERE product_url = :url"),
            {"url": INVALID_URL},
        ).fetchone()

        if not row:
            print(f"No master_product found for URL: {INVALID_URL}", flush=True)
            return 0, 0

        print(
            f"Found: id={row.id}  {row.estate_name} / {row.retailer} / "
            f"vintage {row.vintage_start} / {row.bottle_size}",
            flush=True,
        )

        count = conn.execute(
            text("SELECT COUNT(*) FROM price_records WHERE master_product_id = :id"),
            {"id": row.id},
        ).scalar()
        print(f"Price records to delete: {count}", flush=True)

        if DRY_RUN:
            print("DRY RUN — no changes made.", flush=True)
            return 0, 0

        conn.execute(
            text("UPDATE master_products SET active = FALSE WHERE id = :id"),
            {"id": row.id},
        )
        deleted = conn.execute(
            text("DELETE FROM price_records WHERE master_product_id = :id"),
            {"id": row.id},
        ).rowcount

        print(f"Deactivated master_product {row.id}, deleted {deleted} price_records.", flush=True)
        return 1, deleted


def clean_gsheet() -> int:
    """Remove rows matching INVALID_URL from the price_records GSheet tab."""
    client = _get_gsheet_client()
    sheet  = client.open(GOOGLE_SHEET_NAME)
    ws     = sheet.worksheet(PRICE_RECORDS_TAB)

    print("Reading price_records GSheet tab...", flush=True)
    all_rows = ws.get_all_values()
    if not all_rows:
        print("Tab is empty.", flush=True)
        return 0

    header = all_rows[0]
    url_col = None
    for candidate in ("Link", "url", "URL", "link"):
        if candidate in header:
            url_col = header.index(candidate)
            break
    if url_col is None:
        print(f"URL column not found in header: {header}", flush=True)
        return 0

    bad_indices = [
        i for i, row in enumerate(all_rows[1:], start=2)
        if (url_col < len(row) and row[url_col].strip() == INVALID_URL)
    ]

    print(f"Found {len(bad_indices)} GSheet rows to remove.", flush=True)
    if not bad_indices or DRY_RUN:
        return len(bad_indices)

    for idx in sorted(bad_indices, reverse=True):
        ws.delete_rows(idx)

    print(f"Deleted {len(bad_indices)} GSheet rows.", flush=True)
    return len(bad_indices)


def main() -> None:
    print(f"=== deactivate_invalid_entry starting (dry_run={DRY_RUN}) ===", flush=True)
    print(f"Target URL: {INVALID_URL}", flush=True)

    print("\nStep 1: DB cleanup...", flush=True)
    mp_updated, pr_deleted = clean_db()

    print("\nStep 2: GSheet cleanup...", flush=True)
    gsheet_removed = clean_gsheet()

    print(
        f"\n=== Done. DB: {mp_updated} master_product deactivated, "
        f"{pr_deleted} price_records deleted. "
        f"GSheet: {gsheet_removed} rows removed. ===",
        flush=True,
    )


if __name__ == "__main__":
    main()
