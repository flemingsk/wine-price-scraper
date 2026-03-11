"""
iDealwine scraper (idealwine.com)

iDealwine has TWO price data sources:

1. FIXED-PRICE SHOP — wines available to buy now
   URL: https://www.idealwine.com/fr/vente-vins-en-ligne/{slug}-{vintage}.jsp
   Approach: Playwright (JS-rendered prices)

2. AUCTION PRICE ESTIMATE — historical auction reference price
   URL: https://www.idealwine.com/fr/prix-vins/index.jsp?nom_vin={name}&millesime={vintage}
   Approach: Playwright (JS-rendered)

The MasterProduct.notes field should contain "type:shop" or "type:estimate"
to select the appropriate mode. Defaults to "shop".

CSS selectors (as of early 2025):
  Shop price:     .prix-ttc, .price, .product-price
  Estimate price: .cote-value, .estimation-price, .price-estimate
"""
import logging
import re

from playwright.sync_api import sync_playwright

from src.scrapers.base import BaseScraper, ScrapeResult
from src.scrapers.browser_utils import get_playwright_context, polite_delay
from src.utils import parse_price

logger = logging.getLogger(__name__)

VINTAGE_RE = re.compile(r"(19|20)\d{2}")

# Price selectors to try in order — iDealwine updates its CSS occasionally
SHOP_PRICE_SELECTORS = [
    ".prix-ttc",
    ".product-price",
    ".price-box .price",
    "span.price",
    "[itemprop='price']",
]

ESTIMATE_PRICE_SELECTORS = [
    ".cote-value",
    ".estimation-price",
    ".price-estimate",
    ".cote .value",
    "td.price",
]

OOS_SIGNALS = ["épuisé", "indisponible", "rupture", "out of stock", "sold out"]


class IdealwineScraper(BaseScraper):
    retailer = "idealwine"

    def scrape(self, product) -> list[ScrapeResult]:
        results = []

        # Determine mode from notes field
        mode = "shop"
        if product.notes and "type:estimate" in product.notes.lower():
            mode = "estimate"

        if product.vintage_start and product.vintage_end:
            vintages = range(product.vintage_start, product.vintage_end + 1)
        else:
            vintages = [None]

        with sync_playwright() as p:
            browser, context = get_playwright_context(p)
            try:
                for vintage in vintages:
                    try:
                        if vintage is not None:
                            url = product.url_template.format(vintage=vintage) if product.url_template else product.product_url
                        else:
                            url = product.product_url

                        result = self._scrape_page(context, url, vintage or 0, mode)
                        if result:
                            results.append(result)
                        polite_delay(3.0, 6.0)

                    except Exception as e:
                        logger.warning(f"iDealwine: failed {product.estate_name} {vintage}: {e}")
                        continue
            finally:
                browser.close()

        return results

    def _scrape_page(self, context, url: str, vintage: int, mode: str) -> ScrapeResult | None:
        page = context.new_page()
        try:
            response = page.goto(url, timeout=60000, wait_until="domcontentloaded")

            if response and response.status == 404:
                return None

            # Wait for price element to appear
            page.wait_for_timeout(3000)

            selectors = SHOP_PRICE_SELECTORS if mode == "shop" else ESTIMATE_PRICE_SELECTORS

            raw_price = None
            for selector in selectors:
                el = page.query_selector(selector)
                if el:
                    raw_price = el.inner_text().strip()
                    if raw_price:
                        break

            if not raw_price:
                logger.warning(f"iDealwine: no price found at {url}")
                return None

            try:
                price_amount, currency = parse_price(raw_price)
            except ValueError as e:
                logger.warning(f"iDealwine: could not parse price '{raw_price}': {e}")
                return None

            # Availability
            page_text = page.inner_text("body").lower()
            availability = not any(signal in page_text for signal in OOS_SIGNALS)

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
