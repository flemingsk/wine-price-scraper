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


def _fix_wrong_estate_names(session) -> None:
    """
    Detect and fix any master_products rows whose estate_name is not canonical.
    Runs before the CSV upsert so the unique-key collision never happens.

    When the CSV corrects a typo the unique constraint (which includes estate_name)
    means the loader would INSERT a new row instead of updating the old one. This
    function catches that: it renames orphaned wrong-named rows and, when a
    correctly-named row already exists, merges their price_records and deletes
    the orphan.
    """
    from sqlalchemy import text as sa_text

    for wrong_name, correct_name in ESTATE_NAME_CANONICAL.items():
        wrong_rows = session.execute(
            sa_text("SELECT id, retailer, vintage_start, bottle_size"
                    " FROM master_products WHERE estate_name = :name"),
            {"name": wrong_name},
        ).fetchall()

        for row in wrong_rows:
            wrong_id = row.id
            correct_row = session.execute(
                sa_text("""
                    SELECT id FROM master_products
                    WHERE estate_name = :name AND retailer = :ret
                      AND vintage_start IS NOT DISTINCT FROM :vs
                      AND bottle_size = :bs
                """),
                {"name": correct_name, "ret": row.retailer,
                 "vs": row.vintage_start, "bs": row.bottle_size},
            ).fetchone()

            if correct_row:
                # Correct row already exists — merge price_records then delete orphan
                correct_id = correct_row.id
                session.execute(sa_text("""
                    UPDATE price_records pr
                    SET master_product_id = :cid
                    WHERE master_product_id = :wid
                      AND NOT EXISTS (
                        SELECT 1 FROM price_records pr2
                        WHERE pr2.master_product_id = :cid
                          AND pr2.vintage IS NOT DISTINCT FROM pr.vintage
                          AND DATE(pr2.fetched_at AT TIME ZONE 'UTC')
                              = DATE(pr.fetched_at AT TIME ZONE 'UTC')
                      )
                """), {"cid": correct_id, "wid": wrong_id})
                session.execute(
                    sa_text("DELETE FROM price_records WHERE master_product_id = :id"),
                    {"id": wrong_id},
                )
                session.execute(
                    sa_text("DELETE FROM master_products WHERE id = :id"),
                    {"id": wrong_id},
                )
                logger.info(
                    f"estate_name fix: merged '{wrong_name}' (id={wrong_id})"
                    f" into '{correct_name}' (id={correct_id})"
                )
            else:
                # No correct row — rename in place (no price_records disruption)
                session.execute(
                    sa_text("UPDATE master_products SET estate_name = :name WHERE id = :id"),
                    {"name": correct_name, "id": wrong_id},
                )
                logger.info(
                    f"estate_name fix: renamed '{wrong_name}' (id={wrong_id})"
                    f" → '{correct_name}'"
                )


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
        # Fix any wrong-named rows BEFORE upserting so the unique key never collides
        _fix_wrong_estate_names(session)
        session.commit()

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
