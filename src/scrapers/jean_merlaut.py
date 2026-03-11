# src/scrapers/jean_merlaut.py
import logging
import requests
from bs4 import BeautifulSoup

from src.scrapers.base import BaseScraper, ScrapeResult
from src.scrapers.browser_utils import REQUESTS_HEADERS, polite_delay
from src.utils import parse_price

logger = logging.getLogger(__name__)


class JeanMerlautScraper(BaseScraper):
    retailer = "jean_merlaut"

    def scrape(self, product) -> list[ScrapeResult]:
        results = []

        vintages = (
            range(product.vintage_start, product.vintage_end + 1)
            if product.vintage_start and product.vintage_end
            else [None]
        )

        for vintage in vintages:
            try:
                if vintage and product.url_template:
                    url = product.url_template.format(vintage=vintage)
                else:
                    url = product.product_url
                if not url:
                    continue

                r = requests.get(url, headers=REQUESTS_HEADERS, timeout=20)
                if r.status_code == 404:
                    continue
                r.raise_for_status()

                soup = BeautifulSoup(r.text, "html.parser")
                price_el = soup.select_one(product.price_selector) if product.price_selector else None
                raw_price = price_el.get_text(strip=True) if price_el else None
                price_amount, currency = parse_price(raw_price) if raw_price else (None, None)

                results.append(ScrapeResult(
                    vintage=vintage or 0,
                    price_amount=price_amount,
                    currency=currency,
                    raw_price_text=raw_price,
                    availability=bool(price_el),
                    url=url,
                    retailer="jean_merlaut",
                ))
                polite_delay(1.5, 3.0)

            except Exception as e:
                logger.warning(f"JeanMerlaut: failed {product.estate_name} {vintage}: {e}")

        return results
