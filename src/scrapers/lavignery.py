"""
La Vignery / Vinodis scraper

La Vignery (lavignery.fr) and Vinodis (vinodis.com) are sister sites
with very similar HTML structure — handled by the same scraper class,
distinguished by the retailer name in MasterProduct.

URL pattern (La Vignery):
  https://www.lavignery.fr/vins/{wine-slug}-{vintage}.html

URL pattern (Vinodis):
  https://www.vinodis.com/fr/{wine-slug}-{vintage}.html

Approach: Static HTML via requests + BeautifulSoup.
Both sites render prices server-side.

Price selectors:
  .price, .product-price, span[itemprop='price'], .our_price_display
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
    ".our_price_display",
    ".price .amount",
    "span.price",
    ".prix-ttc",
]

OOS_SIGNALS = ["épuisé", "indisponible", "rupture de stock", "non disponible"]
OOS_CLASSES = ["out-of-stock", "unavailable", "product-unavailable"]


class LaVigneryScraper(BaseScraper):
    """Handles both lavignery.fr and vinodis.com — set retailer name accordingly."""
    retailer = "lavignery"  # override with "vinodis" for Vinodis products

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

                result = self._scrape_single(url, vintage or 0, product.price_selector)
                if result:
                    results.append(result)
                polite_delay(2.0, 5.0)

            except Exception as e:
                logger.warning(f"{self.retailer}: failed {product.estate_name} {vintage}: {e}")
                continue

        return results

    def _scrape_single(self, url: str, vintage: int, css_selector: str | None) -> ScrapeResult | None:
        r = requests.get(url, headers=REQUESTS_HEADERS, timeout=20)

        if r.status_code == 404:
            return None
        r.raise_for_status()

        soup = BeautifulSoup(r.text, "html.parser")

        # Try product-specific selector first, then fallback list
        selectors_to_try = []
        if css_selector:
            selectors_to_try.append(css_selector)
        selectors_to_try.extend(PRICE_SELECTORS)

        price_el = None
        raw_price = None
        for selector in selectors_to_try:
            price_el = soup.select_one(selector)
            if price_el:
                raw_price = price_el.get("content") or price_el.get_text(strip=True)
                if raw_price:
                    break

        if not raw_price:
            logger.warning(f"{self.retailer}: no price at {url}")
            return None

        try:
            price_amount, currency = parse_price(raw_price)
        except ValueError as e:
            logger.warning(f"{self.retailer}: could not parse '{raw_price}': {e}")
            return None

        # Availability
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


class VinodisScraper(LaVigneryScraper):
    """Vinodis — same logic as La Vignery, different retailer name."""
    retailer = "vinodis"
