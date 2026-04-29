import csv
import logging
import os

from sqlalchemy.dialects.postgresql import insert as pg_insert

from .db import SessionLocal
from .models import MasterProduct

logger = logging.getLogger(__name__)

MASTER_PRODUCTS_CSV = os.path.join(os.path.dirname(__file__), "..", "master_products.csv")

# Canonical estate names — normalised at load time so DB stays consistent
# even if a future CSV edit drifts from the standard spelling.
# Only standard 75 cl bottles are tracked — magnums and other formats skew pricing.
STANDARD_BOTTLE_SIZES = {"0.75l", "75cl", "0.75", "75"}

ESTATE_NAME_CANONICAL: dict[str, str] = {
    "Chateau Malartic Lagraviere":  "Chateau Malartic-Lagraviere",
    "Chateau Sociando Mallet":      "Chateau Sociando-Mallet",
}


def main():
    csv_path = os.path.abspath(MASTER_PRODUCTS_CSV)
    if not os.path.exists(csv_path):
        logger.warning(f"master_products.csv not found at {csv_path}, skipping load")
        return

    rows = []
    with open(csv_path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        headers = reader.fieldnames
        logger.info(f"CSV headers detected: {headers}")
        for row in reader:
            rows.append(row)

    if not rows:
        logger.info("No rows in master_products.csv, nothing to load")
        return

    records = []
    skipped_format = 0
    for row in rows:
        vintage_start = row.get("vintage_start", "").strip()
        vintage_end = row.get("vintage_end", "").strip()
        active = row.get("active", "true").strip().lower() not in ("false", "0", "no")

        bottle_size = row.get("bottle_size", "0.75L").strip()
        if bottle_size.lower() not in STANDARD_BOTTLE_SIZES:
            logger.warning(
                f"Skipping non-75cl row: {row.get('estate_name')} "
                f"{row.get('retailer')} {vintage_start} (bottle_size={bottle_size!r})"
            )
            skipped_format += 1
            continue

        raw_name = row["estate_name"].strip()
        records.append({
            "estate_name":            ESTATE_NAME_CANONICAL.get(raw_name, raw_name),
            "retailer":               row["retailer"].strip(),
            "product_url":            row.get("product_url", "").strip() or None,
            "url_template":           row.get("url_template", "").strip() or None,
            "price_selector":         row.get("price_selector", "").strip() or None,
            "availability_selector":  row.get("availability_selector", "").strip() or None,
            "vintage_start":          int(vintage_start) if vintage_start else None,
            "vintage_end":            int(vintage_end) if vintage_end else None,
            "wine_color":             row.get("wine_color", "").strip() or "Rouge",
            "bottle_size":            row.get("bottle_size", "0.75L").strip(),
            "active":                 active,
            "notes":                  row.get("notes", "").strip() or None,
        })

    # Deduplicate: if the CSV has duplicate rows for the same unique key
    # (retailer, estate_name, vintage_start, bottle_size), keep the last occurrence.
    # This also prevents "ON CONFLICT DO UPDATE command cannot affect row a second time".
    seen: dict[tuple, dict] = {}
    dupes = 0
    for rec in records:
        key = (rec["retailer"], rec["estate_name"], rec["vintage_start"], rec["bottle_size"])
        if key in seen:
            dupes += 1
        seen[key] = rec
    if dupes:
        logger.warning(f"Deduplicated {dupes} duplicate rows from CSV (kept last occurrence)")
    records = list(seen.values())

    session = SessionLocal()
    try:
        stmt = pg_insert(MasterProduct).values(records)
        # ON CONFLICT DO UPDATE — keep mutable fields in sync with the CSV so that
        # fixes to selectors, URLs, active flag, wine_color etc. are applied on
        # every run.  The unique-key columns (retailer, estate_name, vintage_start,
        # bottle_size) are never overwritten.
        stmt = stmt.on_conflict_do_update(
            constraint="uq_master_product",
            set_={
                "product_url":           stmt.excluded.product_url,
                "url_template":          stmt.excluded.url_template,
                "price_selector":        stmt.excluded.price_selector,
                "availability_selector": stmt.excluded.availability_selector,
                "vintage_end":           stmt.excluded.vintage_end,
                "wine_color":            stmt.excluded.wine_color,
                "active":                stmt.excluded.active,
                "notes":                 stmt.excluded.notes,
            },
        )
        result = session.execute(stmt)
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()

    inserted = result.rowcount if result.rowcount >= 0 else "unknown"
    skipped = len(records) - (result.rowcount if result.rowcount >= 0 else 0)
    logger.info(
        f"master_products load complete: {inserted} inserted, "
        f"{skipped} already existed (skipped)"
        + (f", {skipped_format} non-75cl rows ignored" if skipped_format else "")
    )
