# src/scrapers/twil.py
"""
Twil scraper — requires Playwright (JS-rendered prices).
Price element: span#product-price-{fragment_id} (content attribute)
Each product URL contains a fragment (e.g. #326562) that maps to the variant.
span.price and span#totalPrice both return wrong values (basket/0.00€ or case total).
"""
import logging
from urllib.parse import urldefrag

from playwright.sync_api import sync_playwright

from src.scrapers.base import BaseScraper, ScrapeResult
from src.scrapers.browser_utils import get_playwright_context, polite_delay
from src.utils import parse_price

logger = logging.getLogger(__name__)

OOS_TEXT = ["épuisé", "indisponible", "rupture de stock", "out of stock"]

PRICE_SELECTORS = [
    # fragment-based selector is built dynamically in _scrape_page
    "span[itemprop='price']",
    ".product-price",
]

# These selectors are known to return wrong values (basket total, 0.00€, or 6-bottle case total)
BAD_SELECTORS = {"span.price", "span#totalPrice"}


class TwilScraper(BaseScraper):
    retailer = "twil"

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
                            logger.warning(f"Twil: no URL for {product.estate_name} {vintage}")
                            continue

                        result = self._scrape_page(context, url, vintage or 0, product)
                        if result:
                            results.append(result)
                        polite_delay(2.0, 4.0)

                    except Exception as e:
                        logger.warning(f"Twil: failed {product.estate_name} {vintage}: {e}")
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

            # Build selector list: fragment-specific first (most reliable),
            # then CSV selector, then generic fallbacks.
            # span.price and span#totalPrice are unreliable — see module docstring.
            selectors = []
            _, fragment = urldefrag(url)
            if fragment:
                selectors.append(f"span#product-price-{fragment}")
            if product.price_selector and product.price_selector not in BAD_SELECTORS:
                selectors.append(product.price_selector)
            selectors.extend(PRICE_SELECTORS)

            raw_price = None
            used_selector = None
            for selector in selectors:
                el = page.query_selector(selector)
                if el:
                    candidate = el.get_attribute("content") or el.inner_text().strip()
                    if candidate:
                        raw_price = candidate
                        used_selector = selector
                        break

            if not raw_price:
                logger.warning(f"Twil: no price found at {url}")
                return None

            try:
                price_amount, currency = parse_price(raw_price)
            except ValueError as e:
                logger.warning(f"Twil: could not parse '{raw_price}' at {url}: {e}")
                return None

            availability = True
            page_text = page.inner_text("body").lower()
            for phrase in OOS_TEXT:
                if phrase in page_text:
                    availability = False
                    break

            logger.info(
                f"Twil: {product.estate_name} {vintage} — "
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
