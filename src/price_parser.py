import requests
from bs4 import BeautifulSoup
from datetime import datetime, UTC

from sqlalchemy.orm import Session

from models import MasterProduct, PriceRecord
from price_parser import parse_eur_price


HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; WinePriceBot/1.0)"
}


def scrape_product(session: Session, product: MasterProduct):
    try:
        r = requests.get(product.product_url, headers=HEADERS, timeout=15)
        r.raise_for_status()
    except Exception as e:
        return PriceRecord(
            master_product_id=product.id,
            site=product.retailer,
            url=product.product_url,
            price_amount=None,
            currency="EUR",
            raw_price_text=None,
            availability=False,
            note=str(e),
            fetched_at=datetime.now(UTC)
        )

    soup = BeautifulSoup(r.text, "html.parser")

    price_el = soup.select_one(product.price_selector)
    if not price_el:
        return PriceRecord(
            master_product_id=product.id,
            site=product.retailer,
            url=product.product_url,
            price_amount=None,
            currency="EUR",
            raw_price_text=None,
            availability=False,
            note="price selector not found",
            fetched_at=datetime.now(UTC)
        )

    raw_price = price_el.get_text(strip=True)
    price_amount = parse_eur_price(raw_price)

    available = True
    if product.availability_selector:
        available = bool(soup.select_one(product.availability_selector))

    return PriceRecord(
        master_product_id=product.id,
        site=product.retailer,
        url=product.product_url,
        price_amount=price_amount,
        currency="EUR",
        raw_price_text=raw_price,
        availability=available,
        note=None,
        fetched_at=datetime.now(UTC)
    )
