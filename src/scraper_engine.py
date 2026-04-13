# src/scraper_engine.py
"""
Scraper engine — orchestrates scraping across all retailers.

Fixes applied:
  FIX 1: Shared Playwright browser per retailer (not per scrape call)
          — browser launched once per retailer thread, reused across products
  FIX 2: Reduced polite delays (handled in browser_utils.py)
  FIX 3: scrape_retailer_products() is the entry point called per thread
          — each thread gets its own DB session, no shared state
"""
import logging
import datetime

from sqlalchemy import func
from sqlalchemy.orm import Session

from src.db import SessionLocal
from src.models import PriceRecord
from src.price_validator import validate_price
from src.scrapers.registry import get_scraper

logger = logging.getLogger(__name__)


def scrape_retailer_products(retailer: str, products: list) -> None:
    """
    FIX 3: Entry point for each parallel retailer thread.
    Creates its own DB session — never shares state with other threads.
    FIX 1: Gets scraper once and reuses it across all products for this retailer.
    """
    # One DB session per thread
    db: Session = SessionLocal()

    try:
        # FIX 1: Get scraper once per retailer — Playwright browser
        # is launched inside the scraper and reused across products
        try:
            scraper = get_scraper(retailer)
        except ValueError as e:
            logger.warning(str(e))
            return

        for product in products:
            logger.info(f"Scraping {product.retailer} | {product.estate_name}")
            try:
                records = scrape_product(product, db, scraper)
                if not records:
                    logger.info(
                        f"No new records today: {product.retailer} | {product.estate_name}"
                    )
                    continue
                for record in records:
                    vintage_label = record.vintage if record.vintage != 0 else "NV"
                    logger.info(
                        f"Saved: {product.retailer} | {product.estate_name} | "
                        f"{vintage_label} | {record.price_amount} {record.currency}"
                    )
            except Exception as e:
                db.rollback()
                logger.error(
                    f"Error scraping {product.retailer} | {product.estate_name}: {e}",
                    exc_info=True,
                )
    finally:
        db.close()


def scrape_product(product, session: Session, scraper=None) -> list[PriceRecord]:
    """
    Scrape all vintages for a MasterProduct and persist results.
    Scraper is passed in from scrape_retailer_products to avoid
    re-instantiating (and re-launching Playwright) for every product.
    """
    today = datetime.datetime.now(datetime.UTC).date()
    saved_records = []

    # Allow scraper to be passed in (FIX 1) or instantiated fresh (backwards compat)
    if scraper is None:
        try:
            scraper = get_scraper(product.retailer)
        except ValueError as e:
            logger.warning(str(e))
            return []

    try:
        results = scraper.scrape(product)
    except Exception as e:
        logger.error(
            f"Scraper crashed for {product.retailer} | {product.estate_name}: {e}",
            exc_info=True,
        )
        return []

    if not results:
        return []

    for result in results:
        try:
            # Validate price against historical median; correct case prices
            result, correction_meta = validate_price(result, product.id, session)

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
                    f"Skipping duplicate: {result.retailer} | "
                    f"{product.estate_name} | {result.vintage}"
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
                price_corrected=correction_meta["corrected"],
                original_price=correction_meta["original_price"],
                correction_reason=correction_meta["reason"],
                fetched_at=datetime.datetime.now(datetime.UTC),
            )

            session.add(record)
            saved_records.append(record)

        except Exception as e:
            logger.error(
                f"Failed to persist record for {product.estate_name} "
                f"vintage {result.vintage}: {e}",
                exc_info=True,
            )
            session.rollback()
            continue

    if saved_records:
        try:
            session.commit()
        except Exception as e:
            logger.error(
                f"Commit failed for {product.estate_name}: {e}",
                exc_info=True,
            )
            session.rollback()

    return saved_records
