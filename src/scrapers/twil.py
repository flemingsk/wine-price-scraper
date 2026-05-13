# src/scrapers/twil.py
"""
Twil scraper — requires Playwright (JS-rendered prices).

Price strategy:
  1. Walk up from span#product-price-{fragment} to find any li.other_offer items
     within the same product section. If a case offer exists (e.g. "12 bouteilles"),
     use the per-bottle price from the largest case — consistent with millesima logic.
  2. If no case offer, use the fragment-based regular price directly.
  3. If the fragment span is absent (stale ID / server error), return None rather
     than falling through to a generic selector that could match a different product.

span.price and span#totalPrice are known to return wrong values and are blocked.
"""
import logging
from urllib.parse import urldefrag

from playwright.sync_api import sync_playwright

from src.scrapers.base import BaseScraper, ScrapeResult
from src.scrapers.browser_utils import get_playwright_context, polite_delay
from src.utils import parse_price

logger = logging.getLogger(__name__)

OOS_TEXT = ["épuisé", "indisponible", "rupture de stock", "out of stock"]

# JS that, given a fragment ID, returns the best case per-bottle price from any
# li.other_offer in the product's section, or null if none found.
_CASE_PRICE_JS = """(fragmentId) => {
    const rp = document.getElementById('product-price-' + fragmentId);
    if (!rp) return null;
    let el = rp;
    while (el && el.tagName !== 'BODY') {
        const offers = el.querySelectorAll('li.other_offer');
        if (offers.length > 0) {
            let bestPrice = null;
            let bestBottles = 0;
            offers.forEach(li => {
                const pe = li.querySelector('.special-price span.price');
                if (!pe) return;
                const txt = li.innerText || '';
                const m = txt.match(/([0-9]+)\\s*bouteille/);
                const bottles = m ? parseInt(m[1]) : 1;
                if (bottles > bestBottles) {
                    bestBottles = bottles;
                    bestPrice = pe.innerText.trim();
                }
            });
            if (bestPrice) return {price: bestPrice, bottles: bestBottles};
            break;
        }
        el = el.parentElement;
    }
    return null;
}"""


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

            _, fragment = urldefrag(url)

            # ── Step 1: look for a case offer (li.other_offer) in the product section ──
            raw_price = None
            used_selector = None

            if fragment:
                case_result = page.evaluate(_CASE_PRICE_JS, fragment)
                if case_result:
                    raw_price = case_result["price"]
                    used_selector = f"other_offer/{case_result['bottles']}bt"

            # ── Step 2: fall back to the fragment-based regular-price span ────────────
            if not raw_price and fragment:
                el = page.query_selector(f"span#product-price-{fragment}")
                if el:
                    raw_price = el.inner_text().strip()
                    used_selector = f"span#product-price-{fragment}"
                else:
                    # Fragment span absent — stale ID or server error.
                    # Do NOT fall through to generic selectors; they would match
                    # a different product on the same page.
                    logger.warning(
                        f"Twil: span#product-price-{fragment} not found at {url} "
                        f"— skipping to avoid wrong-product price"
                    )
                    return None

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
