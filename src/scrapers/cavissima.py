"""
Cavissima scraper (cavissima.com)

URL pattern:  https://www.cavissima.com/vins/{wine-slug}/{vintage}/
              e.g. https://www.cavissima.com/vins/petrus/2018/

Approach: Static HTML via requests + BeautifulSoup.
Cavissima has clean, structured HTML — prices are server-rendered.

Price selectors (as of 2025):
  .product-price, .prix, .price-final, span[itemprop='price']

Availability: stock indicator text or element class.
"""
import logging
import requests
from bs4 import BeautifulSoup

from src.scrapers.base import BaseScraper, ScrapeResult
from src.scrapers.browser_utils import REQUESTS_HEADERS, polite_delay
from src.utils import parse_price

logger = logging.getLogger(__name__)

PRICE_SELECTORS = [
    "span[itemprop='price']",
    ".product-price",
    ".prix",
    ".price-final",
    ".our_price_display",
    ".regular-price span.price",
]

OOS_SIGNALS = ["épuisé", "indisponible", "rupture de stock", "out of stock"]
OOS_CLASSES = ["out-of-stock", "product-unavailable"]


class CavissimaScraper(BaseScraper):
    retailer = "cavissima"

    def scrape(self, product) -> list[ScrapeResult]:
        results = []

        if product.vintage_start and product.vintage_end:
            vintages = range(product.vintage_start, product.vintage_end + 1)
        else:
            vintages = [None]

        for vintage in vintages:
            try:
                if vintage is not None and product.url_template:
                    url = product.url_template.format(vintage=vintage)
                else:
                    url = product.product_url

                result = self._scrape_single(url, vintage or 0)
                if result:
                    results.append(result)
                polite_delay(2.0, 4.5)

            except Exception as e:
                logger.warning(f"Cavissima: failed {product.estate_name} {vintage}: {e}")
                continue

        return results

    def _scrape_single(self, url: str, vintage: int) -> ScrapeResult | None:
        r = requests.get(url, headers=REQUESTS_HEADERS, timeout=20)

        if r.status_code == 404:
            return None
        r.raise_for_status()

        soup = BeautifulSoup(r.text, "html.parser")

        # --- Price ---
        price_el = None
        raw_price = None

        for selector in PRICE_SELECTORS:
            price_el = soup.select_one(selector)
            if price_el:
                # Try content attribute (microdata) first
                raw_price = price_el.get("content") or price_el.get_text(strip=True)
                if raw_price:
                    break

        if not raw_price:
            logger.warning(f"Cavissima: no price at {url}")
            return None

        try:
            price_amount, currency = parse_price(raw_price)
        except ValueError as e:
            logger.warning(f"Cavissima: could not parse '{raw_price}' at {url}: {e}")
            return None

        # --- Availability ---
        availability = True
        page_text = soup.get_text(" ", strip=True).lower()

        for cls in OOS_CLASSES:
            if soup.select_one(f".{cls}"):
                availability = False
                break

        if availability:
            for phrase in OOS_SIGNALS:
                if phrase in page_text:
                    availability = False
                    break

        return ScrapeResult(
            vintage=vintage,
            price_amount=price_amount,
            currency=currency,
            raw_price_text=str(raw_price),
            availability=availability,
            url=url,
            retailer=self.retailer,
        )
