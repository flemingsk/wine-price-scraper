import logging
from apscheduler.schedulers.blocking import BlockingScheduler
from sqlalchemy.orm import Session

from .db import SessionLocal, init_db
from .models import MasterProduct
from .scraper_engine import scrape_product
from .export_to_gsheet import export_to_gsheet



logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)


def run_once():
    db: Session = SessionLocal()

    products = db.query(MasterProduct).all()

    for product in products:
        logging.info(
            f"Scraping {product.retailer} | {product.estate_name}"
        )

        try:
            records = scrape_product(product)

            if records == []:
                logging.info(
                    f"No new records today: {product.retailer} | {product.estate_name}"
                )
                continue

            for record in records:
                logging.info(
                    f"Saved: {product.retailer} | {product.estate_name} | {record.note}"
                )

        except Exception as e:
            db.rollback()
            logging.error(
                f"Error scraping {product.retailer} | {product.estate_name}: {e}"
            )

    db.close()


def main():
    init_db()

    scheduler = BlockingScheduler(timezone="Europe/Paris")
    scheduler.add_job(run_once, "cron", hour=10, minute=0)

    logging.info("Scheduler started (daily at 10:00 CET)")
    run_once()
    export_to_gsheet()
    scheduler.start()



if __name__ == "__main__":
    main()
