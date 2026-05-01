# src/cleanup_invalid_cavissima_blanc.py
"""
One-off: delete price_records for invalid cavissima Blanc master_products.

These 10 entries point to 404 pages or rouge product URLs — the prices
stored against them are wrong (scraped from redirect targets).

The master_products rows are now set to active=FALSE in the CSV.
This script deletes their historical price_records from the DB.

Run via GitHub Actions: cleanup_invalid_cavissima_blanc workflow.
"""
from __future__ import annotations

import logging
import os

from sqlalchemy import text

from src.db import engine

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

INVALID_URLS = [
    # Fieuzal 2015 — rouge URL, mislabelled as Blanc
    "https://www.cavissima.com/products/chateau-de-fieuzal-2015",
    # Malartic-Lagraviere 2020 — rouge URL (slug 2025), mislabelled as Blanc
    "https://www.cavissima.com/products/chateau-malartic-lagraviere-2025",
    # Carbonnieux Blanc 2015–2023 (excl. 2019/2024) — all 404 pages
    "https://www.cavissima.com/products/chateau-carbonnieux-blanc-2015",
    "https://www.cavissima.com/products/chateau-carbonnieux-blanc-2016",
    "https://www.cavissima.com/products/chateau-carbonnieux-blanc-2017",
    "https://www.cavissima.com/products/chateau-carbonnieux-blanc-2018",
    "https://www.cavissima.com/products/chateau-carbonnieux-blanc-2020",
    "https://www.cavissima.com/products/chateau-carbonnieux-blanc-2021",
    "https://www.cavissima.com/products/chateau-carbonnieux-blanc-2022",
    "https://www.cavissima.com/products/chateau-carbonnieux-blanc-2023",
    # Fieuzal Blanc 2020–2022 — blanc URL redirects to rouge product page
    "https://www.cavissima.com/products/chateau-de-fieuzal-blanc-2020",
    "https://www.cavissima.com/products/chateau-de-fieuzal-blanc-2021",
    "https://www.cavissima.com/products/chateau-de-fieuzal-blanc-2022",
    # Olivier Blanc 2018–2022 — blanc URL redirects to rouge product page
    "https://www.cavissima.com/products/chateau-olivier-blanc-2018",
    "https://www.cavissima.com/products/chateau-olivier-blanc-2019",
    "https://www.cavissima.com/products/chateau-olivier-blanc-2020",
    "https://www.cavissima.com/products/chateau-olivier-blanc-2021",
    "https://www.cavissima.com/products/chateau-olivier-blanc-2022",
]

DRY_RUN = os.getenv("DRY_RUN", "").lower() in ("1", "true", "yes")


def main() -> None:
    print(f"=== cleanup_invalid_cavissima_blanc starting (dry_run={DRY_RUN}) ===", flush=True)

    with engine.begin() as conn:
        # Find the master_product IDs for these URLs
        rows = conn.execute(
            text("SELECT id, estate_name, retailer, vintage_start, product_url FROM master_products WHERE product_url = ANY(:urls)"),
            {"urls": INVALID_URLS},
        ).fetchall()

        if not rows:
            print("No matching master_products found — nothing to clean up.", flush=True)
            return

        mp_ids = [r.id for r in rows]
        print(f"Found {len(mp_ids)} matching master_products:", flush=True)
        for r in rows:
            print(f"  id={r.id}  {r.estate_name} / {r.retailer} / vintage {r.vintage_start}  {r.product_url}", flush=True)

        # Count price_records to be deleted
        count_row = conn.execute(
            text("SELECT COUNT(*) FROM price_records WHERE master_product_id = ANY(:ids)"),
            {"ids": mp_ids},
        ).scalar()
        print(f"\nPrice records to delete: {count_row}", flush=True)

        if DRY_RUN:
            print("DRY RUN — no changes made.", flush=True)
        else:
            result = conn.execute(
                text("DELETE FROM price_records WHERE master_product_id = ANY(:ids)"),
                {"ids": mp_ids},
            )
            print(f"Deleted {result.rowcount} price_records.", flush=True)

    print("=== cleanup_invalid_cavissima_blanc complete ===", flush=True)


if __name__ == "__main__":
    main()
