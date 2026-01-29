import requests
import datetime
from bs4 import BeautifulSoup
from sqlalchemy import func

from src.models import PriceRecord
from src.db import SessionLocal
from src.utils import parse_price
from src.scrapers.vinatis import scrape_vinatis_price

HEADERS = {
    "User-Agent": "Mozilla/5.0"
}


def scrape_product(product):
    session = SessionLocal()
    records = []

    try:
        today = datetime.datetime.now(datetime.UTC).date()

        # -------------------------
        # Determine vintages to scrape
        # -------------------------
        if product.url_template and product.vintage_start and product.vintage_end:
            vintages = range(product.vintage_start, product.vintage_end + 1)
        else:
            vintages = [None]

        for vintage in vintages:
            # Build URL
            if vintage is not None:
                url = product.url_template.format(vintage=vintage)
            else:
                url = product.product_url

            # -------------------------
            # Daily deduplication per vintage
            # -------------------------
            existing = (
                session.query(PriceRecord)
                .filter(
                    PriceRecord.master_product_id == product.id,
                    PriceRecord.vintage == vintage,
                    func.date(PriceRecord.fetched_at) == today,
                )
                .first()
            )

            if existing:
                continue

            # -------------------------
            # Scrape
            # -------------------------
            try:
                if product.retailer == "vinatis":
                    price_amount, currency, raw_price, availability = (
                        scrape_vinatis_price(url, product.price_selector)
                    )
                else:
                    r = requests.get(url, headers=HEADERS, timeout=20)
                    if r.status_code == 404:
                        continue
                    r.raise_for_status()

                    soup = BeautifulSoup(r.text, "html.parser")
                    price_el = soup.select_one(product.price_selector)

                    if not price_el:
                        continue

                    raw_price = price_el.get_text(strip=True)
                    price_amount, currency = parse_price(raw_price)
                    availability = True

            except Exception:
                continue

            record = PriceRecord(
                master_product_id=product.id,
                site=product.retailer,
                url=url,
                vintage=vintage,
                wine_color="Rouge",
                price_amount=price_amount,
                currency=currency,
                raw_price_text=raw_price,
                availability=availability,
                fetched_at=datetime.datetime.now(datetime.UTC),
            )

            session.add(record)
            records.append(record)

        if records:
            session.commit()

        return records

    finally:
        session.close()
