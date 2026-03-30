# src/scrapers/cavissima.py
"""
Cavissima scraper — requires Playwright (JS-rendered prices on Shopify).

Format tiles use <label> elements:
  <label>6 x 75cl<small>258,00€</small></label>

Strategy:
  1. Load page with Playwright
  2. Find all format label tiles matching 75cl
  3. Pick largest case (12 > 6 > 1), divide total by bottle count
  4. Fall back to CSS selector if no tiles found
"""
import logging
import re

from playwright.sync_api import sync_playwright

from src.scrapers.base import BaseScraper, ScrapeResult
from src.scrapers.browser_utils import get_playwright_context, polite_delay
from src.utils import parse_price

logger = logging.getLogger(__name__)

OOS_TEXT = ["épuisé", "indisponible", "rupture de stock", "out of stock"]

CASE_RE   = re.compile(r'^(\d+)\s*[x×]\s*75\s*cl', re.IGNORECASE)
SINGLE_RE = re.compile(r'^75\s*cl', re.IGNORECASE)

FALLBACK_SELECTORS = [
    "span.price-item__unit",
    "[data-product-price]",
    "span.price",
    "span[itemprop='price']",
]


def parse_tiles(page) -> list[dict]:
    """
    Find all 75cl format tiles on the page via Playwright.
    Returns list of {label, bottle_count, total_price, unit_price, currency}
    """
    tiles = []
    labels = page.query_selector_all("label")

    for label in labels:
        small_el = label.query_selector("small")
        if not small_el:
            continue

        price_text = small_el.inner_text().strip()
        full_text  = label.inner_text().strip()
        label_text = full_text.replace(price_text, "").strip().replace("\xa0", " ")

        case_match   = CASE_RE.match(label_text)
        single_match = SINGLE_RE.match(label_text)

        if case_match:
            bottle_count = int(case_match.group(1))
        elif single_match:
            bottle_count = 1
        else:
            continue

        try:
            total_price, currency = parse_price(price_text)
        except ValueError:
            continue

        unit_price = round(float(total_price) / bottle_count, 2)
        tiles.append({
            "label":        label_text,
            "bottle_count": bottle_count,
            "total_price":  float(total_price),
            "unit_price":   unit_price,
            "currency":     currency,
        })

    return tiles


class CavissimaScraper(BaseScraper):
    retailer = "cavissima"

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
                            logger.warning(f"Cavissima: no URL for {product.estate_name} {vintage}")
                            continue

                        result = self._scrape_page(context, url, vintage or 0, product)
                        if result:
                            results.append(result)
                        polite_delay(2.0, 4.0)

                    except Exception as e:
                        logger.warning(f"Cavissima: failed {product.estate_name} {vintage}: {e}")
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

            # --- Try tile-based extraction first ---
            tiles = parse_tiles(page)

            if tiles:
                for t in tiles:
                    logger.debug(
                        f"Cavissima: [{product.estate_name} {vintage}] "
                        f"'{t['label']}' total={t['total_price']:.2f} "
                        f"→ unit={t['unit_price']:.2f} {t['currency']}"
                    )

                best = max(tiles, key=lambda t: t["bottle_count"])
                price_amount   = best["unit_price"]
                currency       = best["currency"]
                raw_price_text = (
                    f"{best['label']} @ {best['total_price']:.2f} {currency} "
                    f"= {price_amount:.2f} {currency}/bottle"
                )
                logger.info(
                    f"Cavissima: {product.estate_name} {vintage} — "
                    f"'{best['label']}' → unit price {price_amount:.2f} {currency}"
                )

            else:
                # --- CSS selector fallback ---
                selectors = []
                if product.price_selector:
                    selectors.append(product.price_selector)
                selectors.extend(FALLBACK_SELECTORS)

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
                    logger.warning(f"Cavissima: no price found at {url}")
                    return None

                try:
                    price_amount, currency = parse_price(raw_price)
                    raw_price_text = raw_price
                    logger.info(
                        f"Cavissima: {product.estate_name} {vintage} — "
                        f"{price_amount} {currency} (selector: '{used_selector}')"
                    )
                except ValueError as e:
                    logger.warning(f"Cavissima: could not parse '{raw_price}' at {url}: {e}")
                    return None

            # --- Availability ---
            availability = True
            page_text = page.inner_text("body").lower()
            for phrase in OOS_TEXT:
                if phrase in page_text:
                    availability = False
                    break

            return ScrapeResult(
                vintage=vintage,
                price_amount=price_amount,
                currency=currency,
                raw_price_text=raw_price_text,
                availability=availability,
                url=url,
                retailer=self.retailer,
            )
        finally:
            page.close()
