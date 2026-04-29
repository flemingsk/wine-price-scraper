# src/scrapers/unjoururvin.py
"""
1jour1vin scraper — Playwright required (React/MUI JS-rendered).

Price is split across two adjacent <p class="MuiTypography-body1"> elements:
  <p ...>35</p><p ...>,90€</p>  →  "35,90€"  →  35.90 EUR

The dynamic MUI hash suffix (e.g. mui-style-15wyjrk) changes on MUI updates,
so we match on the stable semantic class MuiTypography-body1 and scan for
adjacent pairs where first = digits, second = decimal+currency.
"""
import logging
import re

from playwright.sync_api import sync_playwright

from src.scrapers.base import BaseScraper, ScrapeResult
from src.scrapers.browser_utils import get_playwright_context, polite_delay
from src.utils import parse_price

logger = logging.getLogger(__name__)

OOS_TEXT = ["épuisé", "indisponible", "rupture de stock", "out of stock", "non disponible"]

# Integer part of price: digits only
_INT_RE  = re.compile(r'^\d+$')
# Decimal+currency part: starts with comma/dot followed by digits and optional currency
_DEC_RE  = re.compile(r'^[,.\d]+[€$£]?$|^[,.\d]+\s*€')


def _extract_price(page) -> tuple[str, str] | tuple[None, None]:
    """
    Find the split-price pattern in MuiTypography-body1 elements.
    Returns (raw_combined_string, selector_description) or (None, None).
    """
    try:
        elements = page.query_selector_all("p.MuiTypography-body1")
        texts = []
        for el in elements:
            try:
                t = el.inner_text().strip().replace('\xa0', ' ')
                if t:
                    texts.append(t)
            except Exception:
                continue

        # Find adjacent pair: integer + decimal/currency
        for i in range(len(texts) - 1):
            if _INT_RE.match(texts[i]) and _DEC_RE.match(texts[i + 1]):
                combined = texts[i] + texts[i + 1]
                return combined, "MuiTypography-body1 pair"

        # Fallback: look for a single element that already contains a full price
        for t in texts:
            if ('€' in t or 'EUR' in t) and re.search(r'\d', t):
                return t, "MuiTypography-body1 single"

    except Exception as e:
        logger.debug(f"1jour1vin _extract_price error: {e}")

    return None, None


class UnJourUnVinScraper(BaseScraper):
    retailer = "1jour1vin"

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
                            logger.warning(f"1jour1vin: no URL for {product.estate_name} {vintage}")
                            continue

                        result = self._scrape_page(context, url, vintage or 0, product)
                        if result:
                            results.append(result)
                        polite_delay(2.0, 4.0)

                    except Exception as e:
                        logger.warning(f"1jour1vin: failed {product.estate_name} {vintage}: {e}")
            finally:
                browser.close()

        return results

    def _scrape_page(self, context, url: str, vintage: int, product) -> ScrapeResult | None:
        page = context.new_page()
        try:
            response = page.goto(url, timeout=60000, wait_until="domcontentloaded")

            if response and response.status == 404:
                logger.info(f"1jour1vin: 404 at {url}")
                return None

            # Wait for React hydration
            page.wait_for_timeout(4000)

            raw_price, used_selector = _extract_price(page)

            if not raw_price:
                logger.warning(f"1jour1vin: no price found at {url}")
                return None

            try:
                price_amount, currency = parse_price(raw_price)
            except ValueError as e:
                logger.warning(f"1jour1vin: could not parse '{raw_price}' at {url}: {e}")
                return None

            availability = True
            try:
                page_text = page.inner_text("body").lower()
                for phrase in OOS_TEXT:
                    if phrase in page_text:
                        availability = False
                        break
            except Exception:
                pass

            logger.info(
                f"1jour1vin: {product.estate_name} {vintage} — "
                f"{price_amount} {currency} via '{used_selector}'"
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
