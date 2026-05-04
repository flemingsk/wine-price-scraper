# src/correct_db_attribution.py
"""
Correct historical price_records attribution errors found in the daily report (2026-05-04).

IMPORTANT: Run AFTER the next full scraper run (so load_master_products has already created
the new correct master_product rows for the renamed chateaunet estates).

Corrections applied:
  1. Chateaunet estate name reassignments — price_records reassigned to correct master_products:
       - Larrivet Haut-Brion 2017 chateaunet → Malartic-Lagraviere 2017
       - Larrivet Haut-Brion 2018 chateaunet → Malartic-Lagraviere 2018
       - Malartic-Lagraviere 2022 chateaunet (had Olivier URL) → Olivier 2022

  2. wineandco URL corrections — price_records.url updated to current correct URL:
       - Bouscaut Blanc 2015: /19197 → /17982
       - Latour-Martillac Blanc 2018: /28461 → /27995

  3. Twil Sociando-Mallet vintage range fix — delete price_records with wrong vintages:
       - #1121375 URL is 2015 vintage only; records stored as vintage=2016 or 2017 are deleted
       - #1137711 URL is 2016 vintage only; records stored as vintage=2017 are deleted
       (Deletion is the only option — correcting vintage would create duplicates.)

  4. vintageandco Carbonnieux 2018 magnum (URL 46961) — deactivate in DB + delete records:
       - URL was removed from CSV but DB record stayed active; prices are for a magnum,
         not a 75cl bottle — bad data.
"""
from __future__ import annotations

import logging
import os

from sqlalchemy import text

from .db import engine
from .export_to_gsheet import _get_gsheet_client, GOOGLE_SHEET_NAME

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

DRY_RUN = os.getenv("DRY_RUN", "").lower() in ("1", "true", "yes")
PRICE_RECORDS_TAB = "price_records"

# ── 1. Chateaunet estate reassignments ────────────────────────────────────────
# (retailer, old_estate, new_estate, vintage_start)
CHATEAUNET_REASSIGNMENTS = [
    ("chateaunet", "Chateau Larrivet Haut-Brion", "Chateau Malartic-Lagraviere", 2017),
    ("chateaunet", "Chateau Larrivet Haut-Brion", "Chateau Malartic-Lagraviere", 2018),
    ("chateaunet", "Chateau Malartic-Lagraviere", "Chateau Olivier", 2022),
]

# ── 2. wineandco URL corrections ──────────────────────────────────────────────
# (retailer, estate_name, vintage_start, old_url, new_url)
WINEANDCO_URL_FIXES = [
    (
        "wineandco", "Chateau Bouscaut", 2015,
        "https://www.wineandco.com/chateau-bouscaut-blanc-2015/19197",
        "https://www.wineandco.com/chateau-bouscaut-blanc-2015/17982",
    ),
    (
        "wineandco", "Chateau Latour Martillac", 2018,
        "https://www.wineandco.com/chateau-latour-martillac-blanc-2018/28461",
        "https://www.wineandco.com/chateau-latour-martillac-blanc-2018/27995",
    ),
]

# ── 3. Twil wrong-vintage deletions ───────────────────────────────────────────
# (product_url, vintages_to_delete)
TWIL_WRONG_VINTAGES = [
    (
        "https://www.twil.fr/france/bordeaux/haut-medoc/chateau-sociando-mallet-wine-1885.html#1121375",
        [2016, 2017],
    ),
    (
        "https://www.twil.fr/france/bordeaux/haut-medoc/chateau-sociando-mallet-wine-1885.html#1137711",
        [2017],
    ),
]

# ── 4. vintageandco magnum to deactivate ──────────────────────────────────────
MAGNUM_URL = "https://www.vintageandco.com/46961.chateau-carbonnieux-2018-pessac-leognan-bordeaux.html"


def _mp_id(conn, retailer: str, estate: str, vintage: int) -> int | None:
    row = conn.execute(
        text("""
            SELECT id FROM master_products
            WHERE retailer = :retailer AND estate_name = :estate AND vintage_start = :vintage
        """),
        {"retailer": retailer, "estate": estate, "vintage": vintage},
    ).fetchone()
    return row.id if row else None


def correct_chateaunet(conn) -> None:
    print("\n--- Step 1: Chateaunet estate reassignments ---", flush=True)
    for retailer, old_estate, new_estate, vintage in CHATEAUNET_REASSIGNMENTS:
        old_id = _mp_id(conn, retailer, old_estate, vintage)
        new_id = _mp_id(conn, retailer, new_estate, vintage)

        if old_id is None:
            print(f"  SKIP ({retailer} / {old_estate} {vintage}): old master_product not found", flush=True)
            continue
        if new_id is None:
            print(
                f"  SKIP ({retailer} / {new_estate} {vintage}): new master_product not found — "
                f"run load_master_products first (next scraper run will do this)", flush=True,
            )
            continue

        n = conn.execute(
            text("SELECT COUNT(*) FROM price_records WHERE master_product_id = :id"),
            {"id": old_id},
        ).scalar()

        if DRY_RUN:
            print(
                f"  DRY RUN: {old_estate} {vintage} → {new_estate}: "
                f"would reassign {n} price_records, deactivate old mp id={old_id}", flush=True,
            )
            continue

        conn.execute(
            text("UPDATE price_records SET master_product_id = :new_id WHERE master_product_id = :old_id"),
            {"new_id": new_id, "old_id": old_id},
        )
        conn.execute(
            text("UPDATE master_products SET active = FALSE WHERE id = :id"),
            {"id": old_id},
        )
        print(
            f"  {old_estate} {vintage} → {new_estate}: reassigned {n} records, "
            f"deactivated old mp id={old_id}", flush=True,
        )


def correct_wineandco_urls(conn) -> None:
    print("\n--- Step 2: wineandco URL corrections ---", flush=True)
    for retailer, estate, vintage, old_url, new_url in WINEANDCO_URL_FIXES:
        mp_id = _mp_id(conn, retailer, estate, vintage)
        if mp_id is None:
            print(f"  SKIP ({estate} {vintage}): master_product not found", flush=True)
            continue

        n = conn.execute(
            text("SELECT COUNT(*) FROM price_records WHERE master_product_id = :id AND url = :url"),
            {"id": mp_id, "url": old_url},
        ).scalar()

        if DRY_RUN:
            print(f"  DRY RUN: {estate} {vintage}: would update {n} url values {old_url} → {new_url}", flush=True)
            continue

        conn.execute(
            text("UPDATE price_records SET url = :new_url WHERE master_product_id = :id AND url = :old_url"),
            {"new_url": new_url, "id": mp_id, "old_url": old_url},
        )
        print(f"  {estate} {vintage}: updated {n} price_records.url → {new_url}", flush=True)


def delete_twil_wrong_vintages(conn) -> None:
    print("\n--- Step 3: Twil wrong-vintage record deletion ---", flush=True)
    for url, vintages in TWIL_WRONG_VINTAGES:
        mp_row = conn.execute(
            text("SELECT id, estate_name, vintage_start FROM master_products WHERE product_url = :url"),
            {"url": url},
        ).fetchone()
        if mp_row is None:
            print(f"  SKIP {url}: master_product not found", flush=True)
            continue

        for v in vintages:
            n = conn.execute(
                text("SELECT COUNT(*) FROM price_records WHERE master_product_id = :id AND vintage = :v"),
                {"id": mp_row.id, "v": v},
            ).scalar()
            if DRY_RUN:
                print(
                    f"  DRY RUN: {mp_row.estate_name} #{url.split('#')[-1]} "
                    f"vintage={v}: would delete {n} wrong-vintage records", flush=True,
                )
                continue
            conn.execute(
                text("DELETE FROM price_records WHERE master_product_id = :id AND vintage = :v"),
                {"id": mp_row.id, "v": v},
            )
            print(
                f"  {mp_row.estate_name} #{url.split('#')[-1]} vintage={v}: deleted {n} records", flush=True,
            )


def deactivate_magnum(conn) -> None:
    print("\n--- Step 4: vintageandco Carbonnieux 2018 magnum deactivation ---", flush=True)
    mp_row = conn.execute(
        text("SELECT id, estate_name, active FROM master_products WHERE product_url = :url"),
        {"url": MAGNUM_URL},
    ).fetchone()

    if mp_row is None:
        print("  Not found in DB — already cleaned up.", flush=True)
        return

    n = conn.execute(
        text("SELECT COUNT(*) FROM price_records WHERE master_product_id = :id"),
        {"id": mp_row.id},
    ).scalar()

    if DRY_RUN:
        print(
            f"  DRY RUN: {mp_row.estate_name} (id={mp_row.id}, active={mp_row.active}): "
            f"would deactivate + delete {n} magnum price_records", flush=True,
        )
        return

    conn.execute(text("UPDATE master_products SET active = FALSE WHERE id = :id"), {"id": mp_row.id})
    conn.execute(text("DELETE FROM price_records WHERE master_product_id = :id"), {"id": mp_row.id})
    print(f"  Deactivated id={mp_row.id}, deleted {n} magnum price_records.", flush=True)


def clean_gsheet_magnum() -> int:
    """Remove GSheet price_records rows for the vintageandco magnum URL."""
    client = _get_gsheet_client()
    sheet  = client.open(GOOGLE_SHEET_NAME)
    ws     = sheet.worksheet(PRICE_RECORDS_TAB)

    all_rows = ws.get_all_values()
    if not all_rows:
        return 0

    header = all_rows[0]
    url_col = None
    for candidate in ("Link", "url", "URL", "link"):
        if candidate in header:
            url_col = header.index(candidate)
            break
    if url_col is None:
        raise RuntimeError(f"URL column not found in header: {header}")

    bad_indices = [
        i for i, row in enumerate(all_rows[1:], start=2)
        if (url_col < len(row) and row[url_col].strip() == MAGNUM_URL)
    ]

    if not bad_indices:
        print("  GSheet: no magnum rows found.", flush=True)
        return 0

    if DRY_RUN:
        print(f"  DRY RUN: GSheet would remove {len(bad_indices)} magnum rows.", flush=True)
        return len(bad_indices)

    for idx in sorted(bad_indices, reverse=True):
        ws.delete_rows(idx)
    print(f"  GSheet: removed {len(bad_indices)} magnum rows.", flush=True)
    return len(bad_indices)


def main() -> None:
    print(f"=== correct_db_attribution starting (dry_run={DRY_RUN}) ===\n", flush=True)

    with engine.begin() as conn:
        correct_chateaunet(conn)
        correct_wineandco_urls(conn)
        delete_twil_wrong_vintages(conn)
        deactivate_magnum(conn)

    print("\n--- Step 4b: GSheet cleanup for magnum ---", flush=True)
    clean_gsheet_magnum()

    print("\n=== Done. ===", flush=True)


if __name__ == "__main__":
    main()
