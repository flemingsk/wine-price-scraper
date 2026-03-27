# src/scrapers/twil.py
import logging
import requests
from bs4 import BeautifulSoup

from src.scrapers.base import BaseScraper, ScrapeResult
from src.scrapers.browser_utils import REQUESTS_HEADERS, polite_delay
from src.utils import parse_price

logger = logging.getLogger(__name__)


class TwilScraper(BaseScraper):
    retailer = "twil"

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
                    logger.warning(f"Twil: no URL for {product.estate_name} {vintage}")
                    continue

                r = requests.get(url, headers=REQUESTS_HEADERS, timeout=20)
                if r.status_code == 404:
                    continue
                r.raise_for_status()

                soup = BeautifulSoup(r.text, "html.parser")

                # TEMP DIAGNOSTICS — remove once bug is identified
                logger.warning(f"Twil DIAG: selector='{product.price_selector}'")

                price_el = soup.select_one(product.price_selector) if product.price_selector else None

                logger.warning(f"Twil DIAG: price_el type={type(price_el).__name__} repr={repr(str(price_el))[:150]}")

                if price_el is None:
                    raw_price = None
                else:
                    raw_price = price_el.get_text(strip=True)
                    logger.warning(f"Twil DIAG: raw_price='{raw_price}'")

                price_amount, currency = parse_price(raw_price) if raw_price else (None, None)

                results.append(ScrapeResult(
                    vintage=vintage or 0,
                    price_amount=price_amount,
                    currency=currency,
                    raw_price_text=raw_price,
                    availability=bool(price_el),
                    url=url,
                    retailer=self.retailer,
                ))
                polite_delay(1.5, 3.0)

            except Exception as e:
                logger.warning(f"Twil: failed {product.estate_name} {vintage}: {e}")

        return results