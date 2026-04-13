"""
Normalize historical Millesima prices from single-bottle to case-unit pricing.

Problem:
  Before ~Mar 10, 2026, Millesima scraper mixed single-bottle and case pricing.
  Current scraper (Mar 10 onward) consistently uses largest-case unit price.
  Need to normalize historical records for trend analysis.

Approach:
  1. Scrape current Millesima pages to get both single and largest-case prices
  2. Calculate discount % per estate × vintage: (single - case) / single
  3. Identify historical records that appear to be single-bottle priced
  4. Apply discount to normalize them to case-unit equivalent
  5. Update DB with corrected prices
"""

import logging
import re
from decimal import Decimal
from datetime import datetime, timedelta

import requests
from bs4 import BeautifulSoup
from sqlalchemy import func

from src.db import SessionLocal
from src.models import MasterProduct, PriceRecord
from src.scrapers.browser_utils import REQUESTS_HEADERS, polite_delay
from src.scrapers.millesima import extract_75cl_tiles
from src.utils import parse_price

logger = logging.getLogger(__name__)

# Cutoff date: records before this need potential normalization
# Mar 27, 2026 is when scraper changed from CSS selector (mixed prices) to tile parsing (largest-case)
NORMALIZATION_CUTOFF = datetime(2026, 3, 27).date()

# Mar 26-27 transition is the best proxy for calculating historical single-bottle to case-unit discount
DISCOUNT_REFERENCE_DATE_BEFORE = datetime(2026, 3, 26).date()
DISCOUNT_REFERENCE_DATE_AFTER = datetime(2026, 3, 27).date()


def get_discount_from_transition():
    """
    Calculate discount per estate×vintage from Mar 26→27 price transition.
    Returns dict: {(estate_name, vintage): discount_pct}

    This is the most accurate proxy since:
    - Mar 26: CSS selector picked whatever price appeared first (single-bottle)
    - Mar 27: Tile parser explicitly picks largest case
    - The ratio between them is the true historical discount
    """
    db = SessionLocal()
    try:
        # Query prices for both dates
        before_records = (
            db.query(
                MasterProduct.estate_name,
                PriceRecord.vintage,
                func.avg(PriceRecord.price_amount).label("avg_price"),
                func.count(PriceRecord.id).label("count"),
            )
            .join(MasterProduct, PriceRecord.master_product_id == MasterProduct.id)
            .filter(
                MasterProduct.retailer == "millesima",
                func.date(PriceRecord.fetched_at) == DISCOUNT_REFERENCE_DATE_BEFORE,
                PriceRecord.price_amount.isnot(None),
                PriceRecord.price_amount > 0,
            )
            .group_by(MasterProduct.estate_name, PriceRecord.vintage)
            .all()
        )

        after_records = (
            db.query(
                MasterProduct.estate_name,
                PriceRecord.vintage,
                func.avg(PriceRecord.price_amount).label("avg_price"),
                func.count(PriceRecord.id).label("count"),
            )
            .join(MasterProduct, PriceRecord.master_product_id == MasterProduct.id)
            .filter(
                MasterProduct.retailer == "millesima",
                func.date(PriceRecord.fetched_at) == DISCOUNT_REFERENCE_DATE_AFTER,
                PriceRecord.price_amount.isnot(None),
                PriceRecord.price_amount > 0,
            )
            .group_by(MasterProduct.estate_name, PriceRecord.vintage)
            .all()
        )

        # Build maps for quick lookup
        before_map = {(r[0], r[1]): float(r[2]) for r in before_records}
        after_map = {(r[0], r[1]): float(r[2]) for r in after_records}

        discounts = {}
        for key in before_map:
            if key in after_map:
                before_price = before_map[key]
                after_price = after_map[key]
                discount_pct = round(((before_price - after_price) / before_price) * 100, 2)
                discounts[key] = {
                    "discount_pct": discount_pct,
                    "before_price": before_price,
                    "after_price": after_price,
                    "source": "Mar 26→27 transition",
                }
                logger.info(
                    f"Discount for {key[0]} {key[1]}: {before_price:.2f} → {after_price:.2f} "
                    f"({discount_pct:.2f}%)"
                )

        return discounts

    finally:
        db.close()


def get_current_millesima_prices():
    """
    Scrape current Millesima prices for all tracked products.
    Returns dict: {(estate_name, vintage): {'single': float, 'case': float, 'discount_pct': float}}
    """
    db = SessionLocal()
    try:
        products = (
            db.query(MasterProduct)
            .filter(MasterProduct.retailer == "millesima")
            .all()
        )
    finally:
        db.close()

    prices = {}

    for product in products:
        if not product.url_template:
            logger.warning(f"No url_template for {product.estate_name}")
            continue

        vintages = (
            range(product.vintage_start, product.vintage_end + 1)
            if product.vintage_start and product.vintage_end
            else [0]
        )

        for vintage in vintages:
            try:
                url = product.url_template.format(vintage=vintage) if vintage != 0 else product.url_template
                logger.info(f"Scraping {product.estate_name} {vintage} from {url}")

                r = requests.get(url, headers=REQUESTS_HEADERS, timeout=20)
                if r.status_code == 404:
                    logger.info(f"  → 404, skipping")
                    continue
                r.raise_for_status()

                soup = BeautifulSoup(r.text, "html.parser")
                tiles, _ = extract_75cl_tiles(soup)

                if not tiles:
                    logger.warning(f"  → no tiles found")
                    continue

                # Extract single and largest-case prices
                single_price = None
                largest_case_price = None

                for tile in tiles:
                    if tile["bottle_count"] == 1:
                        single_price = tile["unit_price"]
                    else:
                        if largest_case_price is None or tile["bottle_count"] > largest_case_price[1]:
                            largest_case_price = (tile["unit_price"], tile["bottle_count"])

                if single_price and largest_case_price:
                    case_price = largest_case_price[0]
                    discount_pct = round(((single_price - case_price) / single_price) * 100, 2)
                    prices[(product.estate_name, vintage)] = {
                        "single": single_price,
                        "case": case_price,
                        "discount_pct": discount_pct,
                        "case_size": largest_case_price[1],
                    }
                    logger.info(
                        f"  → single: {single_price:.2f}, case({largest_case_price[1]}): {case_price:.2f}, "
                        f"discount: {discount_pct:.1f}%"
                    )
                else:
                    logger.warning(f"  → incomplete pricing (single={single_price}, case={largest_case_price})")

                polite_delay(2.0, 4.0)

            except Exception as e:
                logger.error(f"Error scraping {product.estate_name} {vintage}: {e}", exc_info=True)

    return prices


def identify_historical_records_to_normalize(all_discounts):
    """
    Query historical Millesima records (pre-cutoff) and normalize using discount rates.
    Discount rates come from Mar 26→27 transition (most accurate) or current scrape.

    Returns list of (record_id, estate_name, vintage, old_price, normalized_price, discount_pct, reason)
    """
    db = SessionLocal()
    try:
        records = (
            db.query(PriceRecord, MasterProduct.estate_name)
            .join(MasterProduct, PriceRecord.master_product_id == MasterProduct.id)
            .filter(
                MasterProduct.retailer == "millesima",
                func.date(PriceRecord.fetched_at) < NORMALIZATION_CUTOFF,
                PriceRecord.price_amount.isnot(None),
                PriceRecord.price_amount > 0,
            )
            .all()
        )
    finally:
        db.close()

    to_normalize = []

    for record, estate_name in records:
        key = (estate_name, record.vintage)

        if key not in all_discounts:
            logger.debug(f"No discount info for {key}, skipping record {record.id}")
            continue

        discount_info = all_discounts[key]
        discount_pct = discount_info["discount_pct"]
        source = discount_info.get("source", "unknown")

        old_price = float(record.price_amount)

        # Normalize by applying the discount
        normalized_price = round(old_price * (1 - discount_pct / 100), 2)
        to_normalize.append({
            "record_id": record.id,
            "estate_name": estate_name,
            "vintage": record.vintage,
            "old_price": old_price,
            "normalized_price": normalized_price,
            "discount_pct": discount_pct,
            "source": source,
            "reason": f"single-bottle → case unit (apply {discount_pct:.2f}% discount from {source})",
            "fetched_at": record.fetched_at,
        })

    return to_normalize


def apply_normalization(records_to_normalize, dry_run=True):
    """
    Update price_records with normalized prices.
    If dry_run=True, just log what would be done.
    """
    if not records_to_normalize:
        logger.info("No records to normalize.")
        return

    logger.info(f"{'[DRY RUN] ' if dry_run else ''}Normalizing {len(records_to_normalize)} records...")

    if dry_run:
        for rec in records_to_normalize:
            logger.info(
                f"  {rec['estate_name']} {rec['vintage']} {rec['fetched_at'].date()}: "
                f"{rec['old_price']:.2f} → {rec['normalized_price']:.2f} "
                f"({rec['discount_pct']:.2f}% discount, {rec['source']})"
            )
        return

    db = SessionLocal()
    try:
        for rec in records_to_normalize:
            record = db.query(PriceRecord).filter(PriceRecord.id == rec["record_id"]).first()
            if record:
                record.price_amount = Decimal(str(rec["normalized_price"]))
                record.raw_price_text = f"{record.raw_price_text} [normalized: {rec['old_price']:.2f} → {rec['normalized_price']:.2f} ({rec['discount_pct']:.2f}% discount, {rec['source']})]"
                db.add(record)

        db.commit()
        logger.info(f"Updated {len(records_to_normalize)} records in database.")

    except Exception as e:
        logger.error(f"Error updating records: {e}", exc_info=True)
        db.rollback()
    finally:
        db.close()


def main(dry_run=True):
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    logger.info("Starting Millesima price normalization...")
    logger.info(f"Normalization cutoff: records before {NORMALIZATION_CUTOFF}")

    # Step 1: Extract discounts from Mar 26→27 transition (ground truth)
    logger.info("\n=== Step 1: Extracting discount rates from Mar 26→27 transition ===")
    transition_discounts = get_discount_from_transition()
    logger.info(f"Got transition-based discounts for {len(transition_discounts)} references")

    # Step 2: Get missing discounts from current scraping
    logger.info("\n=== Step 2: Scraping current prices for missing references ===")
    current_prices = get_current_millesima_prices()
    logger.info(f"Got current prices for {len(current_prices)} estate×vintage combinations")

    # Combine discounts: prefer transition, fall back to current
    all_discounts = {**transition_discounts}
    for key, info in current_prices.items():
        if key not in all_discounts:
            all_discounts[key] = info
    logger.info(f"Total discount rates available: {len(all_discounts)}")

    # Step 3: Identify records to normalize
    logger.info("\n=== Step 3: Identifying historical records to normalize ===")
    records_to_normalize = identify_historical_records_to_normalize(all_discounts)
    logger.info(f"Identified {len(records_to_normalize)} records needing normalization")

    # Step 4: Apply normalization
    logger.info(f"\n=== Step 4: Applying normalization ({'DRY RUN' if dry_run else 'LIVE UPDATE'}) ===")
    apply_normalization(records_to_normalize, dry_run=dry_run)

    logger.info("\nDone!")


if __name__ == "__main__":
    import sys
    dry_run = "--live" not in sys.argv
    main(dry_run=dry_run)
