import logging
from sqlalchemy.orm import Session

from .db import SessionLocal, init_db
from .models import MasterProduct
from .scraper_engine import scrape_product
from .export_to_gsheet import export_to_gsheet
from .load_master_products import main as load_master_products

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)


def run_once():
    db: Session = SessionLocal()

    try:
        products = db.query(MasterProduct).all()

        for product in products:
            logging.info(
                f"Scraping {product.retailer} | {product.estate_name}"
            )

            try:
                records = scrape_product(product)

                if not records:
                    logging.info(
                        f"No new records today: {product.retailer} | {product.estate_name}"
                    )
                    continue

                for record in records:
                    vintage_label = record.vintage if record.vintage != 0 else "NV"
                    logging.info(
                        f"Saved: {product.retailer} | {product.estate_name} | {record.vintage}"
                    )

            except Exception as e:
                db.rollback()
                logging.error(
                    f"Error scraping {product.retailer} | {product.estate_name}: {e}",
                    exc_info=True,
                )

    finally:
        db.close()


def main():
    logging.info("Starting daily wine price scraper")

    init_db()
    load_master_products()
    run_once()
    export_to_gsheet()

    logging.info("Scraper finished successfully")


if __name__ == "__main__":
    main()
