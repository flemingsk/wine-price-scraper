# src/scrapers/generic.py
"""
Generic static HTML scraper — base class for simple retailers.

Used by all retailers where:
- Price is in static HTML (no JS rendering needed)
- A CSS selector identifies the price element
- Each SKU has a product_url or url_template

Retailers using this: jean_merlaut, twelvebouteilles, cavissima,
lavignery, vinodis, twil, chateaunet, wineandco, aries, wineclub, dubecq
"""
import logging
import re

import requests
from bs4 import BeautifulSoup

from src.scrapers.base import BaseScraper, ScrapeResult
from src.scrapers.browser_utils import REQUESTS_HEADERS, polite_delay
from src.utils import parse_price

logger = logging.getLogger(__name__)

OOS_TEXT = [
    "épuisé", "indisponible", "rupture de stock", "out of stock",
    "sold out", "non disponible", "en rupture", "product-unavailable",
]

# Generic fallback selectors tried if the CSV selector fails
GENERIC_FALLBACK_SELECTORS = [
    "span[itemprop='price']",
    ".product-price",
    ".our_price_display",
    ".prix",
    "span.price",
    ".price .amount",
    ".prix-ttc",
    ".price-final",
    "[data-price]",
]


class GenericStaticScraper(BaseScraper):
    """
    Ready-to-use scraper for simple static HTML retailers.
    Subclasses only need to set `retailer`.
    Optionally override FALLBACK_SELECTORS for site-specific selectors.
    """
    retailer = ""
    FALLBACK_SELECTORS: list[str] = []  # override in subclass if needed

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
                    logger.warning(f"{self.retailer}: no URL for {product.estate_name} {vintage}")
                    continue

                r = requests.get(url, headers=REQUESTS_HEADERS, timeout=20)
                if r.status_code == 404:
                    logger.info(f"{self.retailer}: 404 at {url} — skipping")
                    continue
                r.raise_for_status()

                soup = BeautifulSoup(r.text, "html.parser")

                # Build selector list: CSV → subclass overrides → generic fallbacks
                selectors = []
                if product.price_selector:
                    selectors.append(product.price_selector)
                selectors.extend(self.FALLBACK_SELECTORS)
                selectors.extend(GENERIC_FALLBACK_SELECTORS)

                price_el      = None
                used_selector = None
                for sel in selectors:
                    price_el = soup.select_one(sel)
                    if price_el:
                        used_selector = sel
                        break

                if not price_el:
                    logger.warning(f"{self.retailer}: no price element found at {url}")
                    polite_delay(1.5, 3.0)
                    continue

                # Support microdata content attribute (e.g. itemprop='price')
                raw_price = price_el.get("content") or price_el.get_text(strip=True)

                if not raw_price:
                    logger.warning(f"{self.retailer}: empty price at {url}")
                    polite_delay(1.5, 3.0)
                    continue

                # Guard: if raw_price looks like HTML, selector matched wrong element
                if raw_price.strip().startswith("<"):
                    logger.error(
                        f"{self.retailer}: selector '{used_selector}' returned HTML, not text — "
                        f"check price_selector in master_products.csv for {product.estate_name}"
                    )
                    polite_delay(1.5, 3.0)
                    continue

                try:
                    price_amount, currency = parse_price(raw_price)
                except ValueError as e:
                    logger.warning(f"{self.retailer}: could not parse '{raw_price}' at {url}: {e}")
                    polite_delay(1.5, 3.0)
                    continue

                # Availability
                availability = True
                page_text = soup.get_text(" ", strip=True).lower()
                for phrase in OOS_TEXT:
                    if phrase in page_text:
                        availability = False
                        break

                logger.info(
                    f"{self.retailer}: {product.estate_name} {vintage} — "
                    f"{price_amount} {currency} (selector: '{used_selector}')"
                )

                results.append(ScrapeResult(
                    vintage=vintage or 0,
                    price_amount=price_amount,
                    currency=currency,
                    raw_price_text=str(raw_price),
                    availability=availability,
                    url=url,
                    retailer=self.retailer,
                ))
                polite_delay(1.5, 3.0)

            except Exception as e:
                logger.warning(f"{self.retailer}: failed {product.estate_name} {vintage}: {e}")

        return results
