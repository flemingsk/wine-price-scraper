import requests
import datetime
from bs4 import BeautifulSoup
from sqlalchemy import func

from src.models import PriceRecord
from src.db import SessionLocal
from src.utils import parse_price
from src.scrapers.vinatis import VinatisScraper  # Playwright-based vinatis scraper

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

        # -------------------------
        # Loop over vintages
        # -------------------------
        for vintage in vintages:
            # Build URL
            if vintage is not None:
                url = product.url_template.format(vintage=vintage)
            else:
                url = product.product_url

            # -------------------------
            # Per-vintage daily deduplication
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
            # Scrape price
            # -------------------------
            try:
                if product.retailer.lower() == "vinatis":
                    price_amount, currency, raw_price, availability = VinatisScraper.scrape_price(url)
                else:
                    r = requests.get(url, headers=HEADERS, timeout=20)
                    if r.status_code == 404:
                        continue  # vintage does not exist
                    r.raise_for_status()

                    soup = BeautifulSoup(r.text, "html.parser")
                    price_el = soup.select_one(product.price_selector)

                    if not price_el:
                        continue

                    raw_price = price_el.get_text(strip=True)
                    price_amount, currency = parse_price(raw_price)
                    availability = True

            except Exception:
                # Never break the whole product because one vintage failed
                continue

            # -------------------------
            # Save record
            # -------------------------
            record = PriceRecord(
                master_product_id=product.id,
                site=product.retailer,
                url=url,
                price_amount=price_amount,
                currency=currency,
                raw_price_text=raw_price,
                availability=availability,
                vintage=vintage if vintage is not None else 0,        # NEW: proper vintage column
                wine_color="Rouge",     # NEW: default color
                fetched_at=datetime.datetime.now(datetime.UTC),
            )

            session.add(record)
            records.append(record)

        if records:
            session.commit()

        return records

    finally:
        session.close()
