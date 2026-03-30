"""
Backfill missing daily price records for Jan–March 2026.

Strategy:
  For each (master_product_id, vintage) combo that has records in the period:
    1. Collect all dated anchor records, sorted by date.
    2. For each consecutive pair of anchors, if their prices are within
       SIMILARITY_PCT of each other, generate one record per missing day
       between them (exclusive), copying the earlier anchor's data.
    3. Skip any date that already has a record (idempotent).

Usage:
    DRY_RUN=1 py -m scripts.backfill_jan_mar        # preview only
    py -m scripts.backfill_jan_mar                  # write to DB
"""

import logging
import os
from datetime import date, timedelta, datetime, timezone

from sqlalchemy import func, text

from src.db import SessionLocal
from src.models import PriceRecord
from src.export_to_gsheet import export_to_gsheet

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)

# ── Config ────────────────────────────────────────────────────────────────────
BACKFILL_START = date(2026, 1, 1)
BACKFILL_END   = date(2026, 3, 29)   # up to but not including today (March 30)
SIMILARITY_PCT = 2.0                  # prices within 2% are considered "the same"
DRY_RUN        = os.getenv("DRY_RUN", "0") != "0"
# ─────────────────────────────────────────────────────────────────────────────


def prices_similar(a: float, b: float) -> bool:
    if a == 0 and b == 0:
        return True
    if a == 0 or b == 0:
        return False
    return abs(a - b) / max(a, b) * 100 <= SIMILARITY_PCT


def noon_utc(d: date) -> datetime:
    return datetime(d.year, d.month, d.day, 12, 0, 0, tzinfo=timezone.utc)


def all_dates_between(start: date, end: date):
    """Yield every date strictly between start and end (exclusive on both ends)."""
    d = start + timedelta(days=1)
    while d < end:
        yield d
        d += timedelta(days=1)


def run():
    session = SessionLocal()
    try:
        # ── 1. Fetch all records in the Jan–March window ──────────────────────
        records = (
            session.query(PriceRecord)
            .filter(
                func.date(PriceRecord.fetched_at) >= BACKFILL_START,
                func.date(PriceRecord.fetched_at) <= BACKFILL_END,
                PriceRecord.price_amount.isnot(None),
            )
            .order_by(PriceRecord.master_product_id, PriceRecord.vintage, PriceRecord.fetched_at)
            .all()
        )

        logger.info(f"Found {len(records)} anchor records in {BACKFILL_START}–{BACKFILL_END}")

        # ── 2. Group by (master_product_id, vintage) ─────────────────────────
        groups: dict[tuple, list[PriceRecord]] = {}
        for r in records:
            key = (r.master_product_id, r.vintage)
            groups.setdefault(key, []).append(r)

        logger.info(f"Across {len(groups)} (product, vintage) combinations")

        # Build a set of existing (master_product_id, vintage, date) for dedup
        existing: set[tuple] = {
            (r.master_product_id, r.vintage, r.fetched_at.date())
            for r in records
        }

        # ── 3. For each group, walk consecutive anchor pairs ──────────────────
        to_insert: list[PriceRecord] = []
        skipped_pairs = 0
        filled_pairs  = 0

        for (mp_id, vintage), anchors in groups.items():
            # Deduplicate to one record per date (keep first if multiple)
            by_date: dict[date, PriceRecord] = {}
            for r in anchors:
                d = r.fetched_at.date()
                if d not in by_date:
                    by_date[d] = r

            dated = sorted(by_date.items())   # [(date, PriceRecord), ...]

            for i in range(len(dated) - 1):
                d_left,  r_left  = dated[i]
                d_right, r_right = dated[i + 1]

                p_left  = float(r_left.price_amount)
                p_right = float(r_right.price_amount)

                if not prices_similar(p_left, p_right):
                    skipped_pairs += 1
                    logger.debug(
                        f"SKIP  [{r_left.site}] mp={mp_id} v={vintage} "
                        f"{d_left}={p_left:.2f} vs {d_right}={p_right:.2f} "
                        f"({abs(p_left-p_right)/max(p_left,p_right)*100:.1f}% diff)"
                    )
                    continue

                missing = [
                    d for d in all_dates_between(d_left, d_right)
                    if (mp_id, vintage, d) not in existing
                ]

                if not missing:
                    continue

                filled_pairs += 1
                logger.info(
                    f"FILL  [{r_left.site}] mp={mp_id} v={vintage} "
                    f"{d_left}→{d_right} ({p_left:.2f} ≈ {p_right:.2f}) "
                    f"→ {len(missing)} missing day(s)"
                )

                for d in missing:
                    new_rec = PriceRecord(
                        master_product_id=r_left.master_product_id,
                        site=r_left.site,
                        url=r_left.url,
                        vintage=r_left.vintage,
                        wine_color=r_left.wine_color,
                        price_amount=r_left.price_amount,
                        currency=r_left.currency,
                        raw_price_text=f"[backfill] {r_left.raw_price_text}",
                        availability=r_left.availability,
                        fetched_at=noon_utc(d),
                    )
                    to_insert.append(new_rec)
                    existing.add((mp_id, vintage, d))   # prevent self-duplication

        # ── 4. Summary + commit ───────────────────────────────────────────────
        logger.info(
            f"\nSummary: {len(to_insert)} records to insert | "
            f"{filled_pairs} pairs filled | {skipped_pairs} pairs skipped (price changed)"
        )

        if not to_insert:
            logger.info("Nothing to insert.")
            return

        if DRY_RUN:
            logger.info("DRY RUN — no changes written to DB.")
            for r in to_insert[:20]:
                logger.info(
                    f"  would insert: mp={r.master_product_id} v={r.vintage} "
                    f"site={r.site} date={r.fetched_at.date()} price={r.price_amount}"
                )
            if len(to_insert) > 20:
                logger.info(f"  ... and {len(to_insert) - 20} more")
        else:
            session.bulk_save_objects(to_insert)
            session.commit()
            logger.info(f"Inserted {len(to_insert)} backfill records.")

            logger.info("Exporting new records to Google Sheet...")
            export_to_gsheet()
            logger.info("Google Sheet export complete.")

    finally:
        session.close()


if __name__ == "__main__":
    if DRY_RUN:
        logger.info("=== DRY RUN MODE (set DRY_RUN=0 to write) ===")
    run()
