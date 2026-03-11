"""
Millesima scraper (millesima.fr)

Price is rendered in static HTML as plain text inside a span.
The data-rbf attribute is Vue.js post-render only — not available to requests/BS4.

Correct selectors derived from live page inspection (March 2026).
"""
import logging
import re

import requests
from bs4 import BeautifulSoup

from src.scrapers.base import BaseScraper, ScrapeResult
from src.scrapers.browser_utils import REQUESTS_HEADERS, polite_delay
from src.utils import parse_price

logger = logging.getLogger(__name__)

# Try these in order — first match wins
PRICE_SELECTORS = [
    "span.product-price",          # primary (observed in live HTML)
    ".product-price span",
    "div.price-box span.price",
    "span.price",
    ".prix_ttc",
    ".product-info-price .price",
]

OOS_TEXT = ["épuisé", "indisponible", "rupture de stock", "out of stock"]


class MillesimaScraper(BaseScraper):
    retailer = "millesima"

    def scrape(self, product) -> list[ScrapeResult]:
        results = []

        if not product.url_template:
            logger.warning(f"Millesima: {product.estate_name} has no url_template")
            return results

        vintages = (
            range(product.vintage_start, product.vintage_end + 1)
            if product.vintage_start and product.vintage_end
            else [None]
        )

        for vintage in vintages:
            try:
                url = product.url_template.format(vintage=vintage) if vintage else product.url_template
                result = self._scrape_single(url, vintage or 0, product)
                if result:
                    results.append(result)
                polite_delay(2.0, 4.0)
            except Exception as e:
                logger.warning(f"Millesima: failed {product.estate_name} {vintage}: {e}")

        return results

    def _scrape_single(self, url: str, vintage: int, product) -> ScrapeResult | None:
        r = requests.get(url, headers=REQUESTS_HEADERS, timeout=20)

        if r.status_code == 404:
            return None
        r.raise_for_status()

        soup = BeautifulSoup(r.text, "html.parser")

        # --- Try CSV selector first, then fallbacks ---
        price_el = None
        selectors_to_try = []

        if product.price_selector:
            selectors_to_try.append(product.price_selector)
        selectors_to_try.extend(PRICE_SELECTORS)

        for selector in selectors_to_try:
            price_el = soup.select_one(selector)
            if price_el:
                logger.debug(f"Millesima: matched selector '{selector}' at {url}")
                break

        # --- Regex fallback: find price pattern directly in HTML ---
        if not price_el:
            # Look for a price pattern like "39,20 €" or "39.20 €" in the page
            match = re.search(
                r'(\d{1,4}[.,]\d{2})\s*(?:€|EUR)',
                soup.get_text(" ", strip=True)
            )
            if match:
                raw_price = match.group(0)
                logger.info(f"Millesima: used regex fallback, found '{raw_price}' at {url}")
                try:
                    price_amount, currency = parse_price(raw_price)
                except ValueError:
                    logger.warning(f"Millesima: could not parse regex price '{raw_price}' at {url}")
                    return None

                return ScrapeResult(
                    vintage=vintage,
                    price_amount=price_amount,
                    currency=currency,
                    raw_price_text=raw_price,
                    availability=True,
                    url=url,
                    retailer=self.retailer,
                )

            logger.warning(f"Millesima: no price element found at {url}")
            return None

        raw_price = price_el.get_text(strip=True)
        if not raw_price:
            return None

        try:
            price_amount, currency = parse_price(raw_price)
        except ValueError as e:
            logger.warning(f"Millesima: could not parse price '{raw_price}' at {url}: {e}")
            return None

        # --- Availability ---
        availability = True
        page_text = soup.get_text(" ", strip=True).lower()
        for phrase in OOS_TEXT:
            if phrase in page_text:
                availability = False
                break

        return ScrapeResult(
            vintage=vintage,
            price_amount=price_amount,
            currency=currency,
            raw_price_text=raw_price,
            availability=availability,
            url=url,
            retailer=self.retailer,
        )
