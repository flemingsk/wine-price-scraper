"""
Millesima scraper (millesima.com / millesima.fr)

URL pattern:  https://www.millesima.fr/{wine-slug}-{vintage}.html
              e.g. https://www.millesima.fr/petrus-2018.html

Approach: Static HTML via requests + BeautifulSoup.
Millesima has very clean, stable HTML — no JavaScript needed for prices.

Price selectors (as of 2025):
  .price-box .price            — primary price
  span.price                   — fallback
  [data-price-amount]          — attribute fallback

Availability: look for "En stock" / "In stock" text or out-of-stock class.
"""
import logging
import re
import time
import random

import requests
from bs4 import BeautifulSoup

from src.scrapers.base import BaseScraper, ScrapeResult
from src.scrapers.browser_utils import REQUESTS_HEADERS, polite_delay
from src.utils import parse_price

logger = logging.getLogger(__name__)

# Price CSS selectors to try in order
PRICE_SELECTORS = [
    ".price-box .price",
    "span.price",
    ".product-info-price .price",
    "[data-price-amount]",
    ".our_price_display",
]

# Out-of-stock indicators
OOS_CLASSES = ["out-of-stock", "rupture", "unavailable", "sold-out"]
OOS_TEXT = ["épuisé", "indisponible", "rupture de stock", "out of stock"]


class MillesimaScraper(BaseScraper):
    retailer = "millesima"

    def scrape(self, product) -> list[ScrapeResult]:
        results = []

        if not product.url_template:
            logger.warning(f"Millesima product {product.estate_name} has no url_template")
            return results

        # Determine vintage range
        if product.vintage_start and product.vintage_end:
            vintages = range(product.vintage_start, product.vintage_end + 1)
        else:
            vintages = [None]

        for vintage in vintages:
            try:
                url = product.url_template.format(vintage=vintage) if vintage else product.url_template
                result = self._scrape_single(url, vintage or 0)
                if result:
                    results.append(result)
                polite_delay(2.0, 4.0)

            except Exception as e:
                logger.warning(f"Millesima: failed {product.estate_name} {vintage}: {e}")
                continue

        return results

    def _scrape_single(self, url: str, vintage: int) -> ScrapeResult | None:
        r = requests.get(url, headers=REQUESTS_HEADERS, timeout=20)

        if r.status_code == 404:
            return None  # vintage not stocked
        r.raise_for_status()

        soup = BeautifulSoup(r.text, "html.parser")

        # --- Price ---
        price_el = None
        for selector in PRICE_SELECTORS:
            price_el = soup.select_one(selector)
            if price_el:
                break

        if not price_el:
            logger.warning(f"Millesima: no price element found at {url}")
            return None

        # Try data-price-amount attribute first (most reliable)
        raw_price = price_el.get("data-price-amount") or price_el.get_text(strip=True)
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

        # Check for out-of-stock classes
        for cls in OOS_CLASSES:
            if soup.select_one(f".{cls}"):
                availability = False
                break

        # Check for out-of-stock text
        if availability:
            for phrase in OOS_TEXT:
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
