"""
Vinatis scraper (vinatis.com)

Vinatis uses heavy JavaScript rendering — requires Playwright.
Prices are loaded dynamically after page initialisation.

URL pattern:  https://www.vinatis.com/achat-vin/{wine-slug}
              or with vintage:  https://www.vinatis.com/achat-vin/{wine-slug}-{vintage}

Price selectors (as of 2025):
  .prix, .price, span.product-price, [itemprop='price']
"""
import logging
import re

from playwright.sync_api import sync_playwright

from src.scrapers.base import BaseScraper, ScrapeResult
from src.scrapers.browser_utils import get_playwright_context, polite_delay
from src.utils import parse_price

logger = logging.getLogger(__name__)

VINTAGE_RE = re.compile(r"\b(19|20)\d{2}\b")

PRICE_SELECTORS = [
    ".prix",
    "span[itemprop='price']",
    ".product-price",
    ".price",
    ".prix-ttc",
    "[data-price]",
]

OOS_SIGNALS = ["épuisé", "indisponible", "rupture", "out of stock"]


class VinatisScraper(BaseScraper):
    retailer = "vinatis"

    def scrape(self, product) -> list[ScrapeResult]:
        results = []

        if product.vintage_start and product.vintage_end:
            vintages = range(product.vintage_start, product.vintage_end + 1)
        else:
            vintages = [None]

        with sync_playwright() as p:
            browser, context = get_playwright_context(p)
            try:
                for vintage in vintages:
                    try:
                        if vintage is not None and product.url_template:
                            url = product.url_template.format(vintage=vintage)
                        else:
                            url = product.product_url

                        result = self._scrape_page(context, url, vintage, product.price_selector)
                        if result:
                            results.append(result)
                        polite_delay(3.0, 6.0)

                    except Exception as e:
                        logger.warning(f"Vinatis: failed {product.estate_name} {vintage}: {e}")
                        continue
            finally:
                browser.close()

        return results

    def _scrape_page(self, context, url: str, vintage: int | None, css_selector: str | None) -> ScrapeResult | None:
        page = context.new_page()
        try:
            response = page.goto(url, timeout=60000, wait_until="domcontentloaded")

            if response and response.status == 404:
                return None

            page.wait_for_timeout(3000)

            # Try product-specific selector first, then fallback list
            selectors_to_try = []
            if css_selector:
                selectors_to_try.append(css_selector)
            selectors_to_try.extend(PRICE_SELECTORS)

            raw_price = None
            for selector in selectors_to_try:
                el = page.query_selector(selector)
                if el:
                    candidate = el.get_attribute("content") or el.inner_text().strip()
                    if candidate:
                        raw_price = candidate
                        break

            if not raw_price:
                logger.warning(f"Vinatis: no price at {url}")
                return None

            try:
                price_amount, currency = parse_price(raw_price)
            except ValueError as e:
                logger.warning(f"Vinatis: could not parse '{raw_price}': {e}")
                return None

            # Vintage detection — scoped to title, not full body
            detected_vintage = vintage or 0
            if detected_vintage == 0:
                for title_selector in ["h1", ".product-title", ".product-name", "title"]:
                    title_el = page.query_selector(title_selector)
                    if title_el:
                        title_text = title_el.inner_text()
                        match = VINTAGE_RE.search(title_text)
                        if match:
                            year = int(match.group())
                            if 1900 <= year <= 2100:
                                detected_vintage = year
                                break

            # Availability
            page_text = page.inner_text("body").lower()
            availability = not any(s in page_text for s in OOS_SIGNALS)

            return ScrapeResult(
                vintage=detected_vintage,
                price_amount=price_amount,
                currency=currency,
                raw_price_text=raw_price,
                availability=availability,
                url=url,
                retailer=self.retailer,
            )
        finally:
            page.close()

    @classmethod
    def scrape_price(cls, url: str, price_selector: str):
        """
        Legacy compatibility shim — used by old scraper_engine.py.
        Prefer using scrape(product) via the new engine.
        """
        with sync_playwright() as p:
            browser, context = get_playwright_context(p)
            try:
                scraper = cls()
                result = scraper._scrape_page(context, url, None, price_selector)
                if result:
                    return result.price_amount, result.currency, result.raw_price_text, result.availability
                raise RuntimeError(f"No price found at {url}")
            finally:
                browser.close()
