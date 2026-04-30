"""
One-time script: fix incorrect cercledemartillac price records.

Two classes of errors:
  1. Records corrected by ÷3 (old validator logic): new_price = stored / 2
     (because stored = original/3; correct divisor is 6, so correct = original/6 = stored/2)
  2. Blanc records from Apr 29+ with uncorrected 6-bottle case prices (>150 €):
     new_price = stored / 6

Usage:
    python fix_cercledemartillac_prices.py           # live run
    python fix_cercledemartillac_prices.py --dry-run # inspect only
"""
import sys
import logging
from dotenv import load_dotenv
from sqlalchemy import text

from src.db import SessionLocal

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

DRY_RUN = "--dry-run" in sys.argv


def main():
    db = SessionLocal()
    try:
        # ── Part 1: ÷3-corrected records (stored = original/3, want original/6) ──
        rows_3 = db.execute(text("""
            SELECT pr.id, pr.price_amount, pr.original_price, pr.correction_reason,
                   mp.estate_name, pr.vintage, pr.fetched_at
            FROM price_records pr
            JOIN master_products mp ON mp.id = pr.master_product_id
            WHERE pr.site = 'cercledemartillac'
              AND pr.price_corrected = TRUE
              AND (pr.correction_reason ILIKE '%÷3%' OR pr.correction_reason ILIKE '%/3%')
        """)).fetchall()

        logger.info(f"Found {len(rows_3)} ÷3-corrected cercledemartillac records to fix")
        for row in rows_3:
            new_price = round(float(row.price_amount) / 2, 2)
            logger.info(
                f"  ID {row.id}: {row.estate_name} v{row.vintage}  "
                f"{float(row.price_amount):.2f} → {new_price:.2f}  (÷3→÷6)  {row.fetched_at}"
            )
            if not DRY_RUN:
                db.execute(text("""
                    UPDATE price_records
                    SET price_amount      = :new_price,
                        correction_reason = 'case price ÷6 (recorrected from ÷3)'
                    WHERE id = :id
                """), {"new_price": new_price, "id": row.id})

        # ── Part 2: Blanc records with uncorrected 6-bottle case price (>150 €) ──
        rows_blanc = db.execute(text("""
            SELECT pr.id, pr.price_amount, mp.estate_name, pr.vintage,
                   pr.wine_color, pr.fetched_at
            FROM price_records pr
            JOIN master_products mp ON mp.id = pr.master_product_id
            WHERE pr.site = 'cercledemartillac'
              AND pr.wine_color = 'Blanc'
              AND pr.price_corrected = FALSE
              AND pr.price_amount > 150
              AND DATE(pr.fetched_at) >= '2026-04-29'
        """)).fetchall()

        logger.info(f"Found {len(rows_blanc)} uncorrected Blanc case-price records to fix")
        for row in rows_blanc:
            new_price = round(float(row.price_amount) / 6, 2)
            logger.info(
                f"  ID {row.id}: {row.estate_name} v{row.vintage} Blanc  "
                f"{float(row.price_amount):.2f} → {new_price:.2f}  (÷6)  {row.fetched_at}"
            )
            if not DRY_RUN:
                db.execute(text("""
                    UPDATE price_records
                    SET price_amount      = :new_price,
                        price_corrected   = TRUE,
                        original_price    = :orig,
                        correction_reason = 'case price ÷6 (retroactive: new product, no history on scrape date)'
                    WHERE id = :id
                """), {"new_price": new_price, "orig": float(row.price_amount), "id": row.id})

        if not DRY_RUN:
            db.commit()
            logger.info("All fixes committed.")
        else:
            logger.info("DRY RUN — no changes written.")

        # ── Part 3: Diagnostic — any other cercledemartillac prices > 100 € ───
        outliers = db.execute(text("""
            SELECT pr.id, mp.estate_name, pr.vintage, pr.wine_color,
                   pr.price_amount, pr.price_corrected, pr.correction_reason,
                   DATE(pr.fetched_at) AS scrape_date
            FROM price_records pr
            JOIN master_products mp ON mp.id = pr.master_product_id
            WHERE pr.site = 'cercledemartillac'
              AND pr.price_amount > 100
            ORDER BY pr.fetched_at DESC
        """)).fetchall()

        if outliers:
            logger.info(f"\nRemaining cercledemartillac prices > 100 € ({len(outliers)}):")
            for row in outliers:
                logger.info(
                    f"  ID {row.id}: {row.estate_name} v{row.vintage} {row.wine_color}  "
                    f"€{float(row.price_amount):.2f}  corrected={row.price_corrected}  "
                    f"reason={row.correction_reason}  date={row.scrape_date}"
                )
        else:
            logger.info("No cercledemartillac prices > 100 € remaining.")

    finally:
        db.close()


if __name__ == "__main__":
    main()
