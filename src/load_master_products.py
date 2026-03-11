import csv
import logging
import os

from sqlalchemy.dialects.postgresql import insert as pg_insert

from .db import SessionLocal
from .models import MasterProduct

logger = logging.getLogger(__name__)

MASTER_PRODUCTS_CSV = os.path.join(os.path.dirname(__file__), "..", "master_products.csv")


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
    for row in rows:
        vintage_start = row.get("vintage_start", "").strip()
        vintage_end = row.get("vintage_end", "").strip()
        active = row.get("active", "true").strip().lower() not in ("false", "0", "no")

        records.append({
            "estate_name":            row["estate_name"].strip(),
            "retailer":               row["retailer"].strip(),
            "product_url":            row.get("product_url", "").strip() or None,
            "url_template":           row.get("url_template", "").strip() or None,
            "price_selector":         row.get("price_selector", "").strip() or None,
            "availability_selector":  row.get("availability_selector", "").strip() or None,
            "vintage_start":          int(vintage_start) if vintage_start else None,
            "vintage_end":            int(vintage_end) if vintage_end else None,
            "wine_color":             row.get("wine_color", "").strip() or None,
            "bottle_size":            row.get("bottle_size", "0.75L").strip(),
            "active":                 active,
            "notes":                  row.get("notes", "").strip() or None,
        })

    session = SessionLocal()
    try:
        stmt = pg_insert(MasterProduct).values(records)
        # ON CONFLICT DO NOTHING — skip rows already matching the unique constraint
        # (retailer, estate_name, vintage_start, bottle_size)
        stmt = stmt.on_conflict_do_nothing(constraint="uq_master_product")
        result = session.execute(stmt)
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()

    inserted = result.rowcount if result.rowcount >= 0 else "unknown"
    skipped = len(records) - (result.rowcount if result.rowcount >= 0 else 0)
    logger.info(f"master_products load complete: {inserted} inserted, {skipped} already existed (skipped)")
