# src/scrapers/e_leclerc.py
"""
E.Leclerc scraper — Playwright required.
Modern JS-rendered French supermarket site.
Price selector: span[data-testid='price'] or .ProductPrice or .price
"""
import logging

from playwright.sync_api import sync_playwright

from src.scrapers.base import BaseScraper, ScrapeResult
from src.scrapers.browser_utils import get_playwright_context, polite_delay
from src.utils import parse_price

logger = logging.getLogger(__name__)

OOS_TEXT = ["épuisé", "indisponible", "rupture de stock", "out of stock", "non disponible"]

PRICE_SELECTORS = [
    "span[data-testid='price']",
    ".ProductPrice",
    ".product-price",
    "span[itemprop='price']",
    ".price",
    "[class*='price']",
    "[class*='Price']",
]


class ELeclercScraper(BaseScraper):
    retailer = "e_leclerc"

    def scrape(self, product) -> list[ScrapeResult]:
        results = []

        vintages = (
            range(product.vintage_start, product.vintage_end + 1)
            if product.vintage_start and product.vintage_end
            else [None]
        )

        with sync_playwright() as p:
            browser, context = get_playwright_context(p)
            try:
                for vintage in vintages:
                    try:
                        url = product.product_url
                        if not url:
                            continue

                        result = self._scrape_page(context, url, vintage or 0, product)
                        if result:
                            results.append(result)
                        polite_delay(1.5, 3.0)

                    except Exception as e:
                        logger.warning(
                            f"e_leclerc: failed {product.estate_name} {vintage}: {e}"
                        )
            finally:
                browser.close()

        return results

    def _scrape_page(self, context, url: str, vintage: int, product) -> ScrapeResult | None:
        page = context.new_page()
        try:
            response = page.goto(url, timeout=60000, wait_until="domcontentloaded")

            if response and response.status in (404, 403):
                logger.info(f"e_leclerc: {response.status} at {url} — skipping")
                return None

            page.wait_for_timeout(2000)

            selectors = []
            if product.price_selector:
                selectors.append(product.price_selector)
            selectors.extend(PRICE_SELECTORS)

            raw_price = None
            used_selector = None
            for selector in selectors:
                el = page.query_selector(selector)
                if el:
                    candidate = el.get_attribute("content") or el.inner_text().strip()
                    if candidate and any(c.isdigit() for c in candidate):
                        raw_price = candidate
                        used_selector = selector
                        break

            if not raw_price:
                logger.warning(f"e_leclerc: no price found at {url}")
                return None

            try:
                price_amount, currency = parse_price(raw_price)
            except ValueError as e:
                logger.warning(f"e_leclerc: could not parse '{raw_price}' at {url}: {e}")
                return None

            availability = True
            page_text = page.inner_text("body").lower()
            for phrase in OOS_TEXT:
                if phrase in page_text:
                    availability = False
                    break

            logger.info(
                f"e_leclerc: {product.estate_name} {vintage} — "
                f"{price_amount} {currency} (selector: '{used_selector}')"
            )

            return ScrapeResult(
                vintage=vintage,
                price_amount=price_amount,
                currency=currency,
                raw_price_text=raw_price,
                availability=availability,
                url=url,
                retailer=self.retailer,
            )
        finally:
            page.close()
