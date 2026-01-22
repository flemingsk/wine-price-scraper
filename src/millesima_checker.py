# millesima_checker.py
import logging
from sqlalchemy.orm import Session

from src import SessionLocal
from src import MasterProduct
from src import scrape_product

logging.basicConfig(level=logging.INFO)


def main():
    session: Session = SessionLocal()

    products = (
        session.query(MasterProduct)
        .filter(MasterProduct.retailer == "millesima")
        .all()
    )

    print(f"Found {len(products)} Millesima products")

    for product in products:
        logging.info(
            f"Checking {product.estate_name} | URL={product.product_url}"
        )

        try:
            record = scrape_product(session, product)

            print(
                {
                    "estate": product.estate_name,
                    "url": record.url,
                    "price": record.price_amount,
                    "currency": record.currency,
                    "raw": record.raw_price_text,
                }
            )

        except Exception as e:
            print(
                f"FAILED: {product.estate_name} | {product.product_url} | {e}"
            )

    session.close()


if __name__ == "__main__":
    main()
