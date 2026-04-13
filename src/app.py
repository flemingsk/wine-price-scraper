# src/app.py
import logging
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed

from .db import SessionLocal, init_db
from .models import MasterProduct
from .scraper_engine import scrape_retailer_products
from .export_to_gsheet import export_to_gsheet, export_daily_report
from .load_master_products import main as load_master_products

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)

# FIX 3: Number of retailers to scrape in parallel.
# Each retailer still scrapes its own products sequentially internally
# so individual sites are never hammered simultaneously.
# Revert to sequential: set MAX_WORKERS = 1
MAX_WORKERS = 8


def run_once():
    # Load all active products grouped by retailer
    db = SessionLocal()
    try:
        products = (
            db.query(MasterProduct)
            .filter(
                MasterProduct.active == True,
                MasterProduct.bottle_size.in_(["0.75L", "75cl"]),
            )
            .all()
        )
    finally:
        db.close()

    by_retailer = defaultdict(list)
    for product in products:
        by_retailer[product.retailer].append(product)

    logging.info(
        f"Scraping {len(products)} products across {len(by_retailer)} retailers "
        f"with up to {MAX_WORKERS} parallel workers"
    )

    # FIX 3: Run each retailer in its own thread
    # Each thread gets its own DB session via SessionLocal()
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {
            executor.submit(
                scrape_retailer_products,
                retailer,
                retailer_products,
            ): retailer
            for retailer, retailer_products in by_retailer.items()
        }

        for future in as_completed(futures):
            retailer = futures[future]
            try:
                future.result()
            except Exception as e:
                logging.error(f"Retailer thread failed for {retailer}: {e}", exc_info=True)


def main():
    logging.info("Starting daily wine price scraper")
    init_db()
    load_master_products()
    run_once()
    export_to_gsheet()
    export_daily_report()
    logging.info("Scraper finished successfully")


if __name__ == "__main__":
    main()
