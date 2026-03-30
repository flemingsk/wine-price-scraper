# src/scrapers/chateaunet.py
"""
Chateaunet scraper — requires Playwright (JS-rendered prices).
Price selector: span[itemprop='price'] (content attribute has clean decimal)
Note: strip tracking parameters from URLs in master_products.csv
      (remove everything from ?_gl= onwards)
"""
import logging

from playwright.sync_api import sync_playwright

from src.scrapers.base import BaseScraper, ScrapeResult
from src.scrapers.browser_utils import get_playwright_context, polite_delay
from src.utils import parse_price

logger = logging.getLogger(__name__)

OOS_TEXT = ["épuisé", "indisponible", "rupture de stock", "out of stock"]

PRICE_SELECTORS = [
    "span[itemprop='price']",
    "span.price",
    ".our_price_display",
    ".product-price",
]


class ChateaunetScraper(BaseScraper):
    retailer = "chateaunet"

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
                        if vintage and product.url_template:
                            url = product.url_template.format(vintage=vintage)
                        else:
                            url = product.product_url
                        if not url:
                            logger.warning(f"chateaunet: no URL for {product.estate_name} {vintage}")
                            continue

                        # Strip tracking parameters — everything from ? onwards if it contains _gl or gclid
                        if "?_gl=" in url or "?_gs" in url:
                            url = url.split("?")[0]

                        result = self._scrape_page(context, url, vintage or 0, product)
                        if result:
                            results.append(result)
                        polite_delay(2.0, 4.0)

                    except Exception as e:
                        logger.warning(f"chateaunet: failed {product.estate_name} {vintage}: {e}")
            finally:
                browser.close()

        return results

    def _scrape_page(self, context, url: str, vintage: int, product) -> ScrapeResult | None:
        page = context.new_page()
        try:
            response = page.goto(url, timeout=60000, wait_until="domcontentloaded")
            if response and response.status == 404:
                return None

            page.wait_for_timeout(3000)

            selectors = []
            if product.price_selector:
                selectors.append(product.price_selector)
            selectors.extend(PRICE_SELECTORS)

            raw_price = None
            used_selector = None
            for selector in selectors:
                el = page.query_selector(selector)
                if el:
                    # Prefer content attribute (clean decimal) over inner text
                    candidate = el.get_attribute("content") or el.inner_text().strip()
                    if candidate:
                        raw_price = candidate
                        used_selector = selector
                        break

            if not raw_price:
                logger.warning(f"chateaunet: no price found at {url}")
                return None

            try:
                price_amount, currency = parse_price(raw_price)
            except ValueError as e:
                logger.warning(f"chateaunet: could not parse '{raw_price}' at {url}: {e}")
                return None

            availability = True
            page_text = page.inner_text("body").lower()
            for phrase in OOS_TEXT:
                if phrase in page_text:
                    availability = False
                    break

            logger.info(
                f"chateaunet: {product.estate_name} {vintage} — "
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
