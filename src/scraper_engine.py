"""
Scraper engine — orchestrates scraping across all retailers.

Uses the scraper registry to dispatch each MasterProduct to the
correct retailer scraper, then persists results to the database.

Key improvements over v1:
- Registry pattern: adding a new retailer = one line in registry.py
- All scrapers return standardised ScrapeResult objects
- Single session passed through (no cross-session issues)
- Per-vintage daily deduplication preserved
- Graceful error isolation: one retailer failing never blocks others
"""
import logging
import datetime

from sqlalchemy import func
from sqlalchemy.orm import Session

from src.models import PriceRecord
from src.scrapers.registry import get_scraper

logger = logging.getLogger(__name__)


def scrape_product(product, session: Session) -> list[PriceRecord]:
    """
    Scrape all vintages for a MasterProduct using the appropriate retailer scraper.
    Saves new PriceRecord rows (deduped per vintage per day) and returns them.
    """
    today = datetime.datetime.now(datetime.UTC).date()
    saved_records = []

    # Dispatch to correct scraper via registry
    try:
        scraper = get_scraper(product.retailer)
    except ValueError as e:
        logger.warning(str(e))
        return []

    # Run the scraper — returns list[ScrapeResult]
    try:
        results = scraper.scrape(product)
    except Exception as e:
        logger.error(f"Scraper crashed for {product.retailer} | {product.estate_name}: {e}", exc_info=True)
        return []

    if not results:
        return []

    # Persist each result with per-vintage daily deduplication
    for result in results:
        try:
            existing = (
                session.query(PriceRecord)
                .filter(
                    PriceRecord.master_product_id == product.id,
                    PriceRecord.vintage == result.vintage,
                    func.date(PriceRecord.fetched_at) == today,
                )
                .first()
            )

            if existing:
                logger.debug(
                    f"Skipping duplicate: {result.retailer} | {product.estate_name} | {result.vintage}"
                )
                continue

            record = PriceRecord(
                master_product_id=product.id,
                site=result.retailer,
                url=result.url,
                price_amount=result.price_amount,
                currency=result.currency,
                raw_price_text=result.raw_price_text,
                availability=result.availability,
                vintage=result.vintage,
                wine_color=product.wine_color or "Rouge",
                fetched_at=datetime.datetime.now(datetime.UTC),
            )

            session.add(record)
            saved_records.append(record)

        except Exception as e:
            logger.error(
                f"Failed to persist record for {product.estate_name} vintage {result.vintage}: {e}",
                exc_info=True,
            )
            session.rollback()
            continue

    if saved_records:
        try:
            session.commit()
        except Exception as e:
            logger.error(f"Commit failed for {product.estate_name}: {e}", exc_info=True)
            session.rollback()

    return saved_records
