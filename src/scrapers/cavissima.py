# src/scrapers/cavissima.py
"""
Cavissima scraper (cavissima.com) — Shopify-based.

Format tiles use <label> elements:
  <label for="...">
    6 x 75cl<small>258,00€</small>
  </label>

Strategy (mirrors Millesima):
  1. Find all <label> elements containing "x 75cl" or "75cl"
  2. Extract bottle count and total price from each
  3. Pick largest case (12 > 6 > 1) for best unit price
  4. Divide total by bottle count → unit price
  5. Fall back to generic CSS selectors if no tiles found
"""
import logging
import re

import requests
from bs4 import BeautifulSoup

from src.scrapers.base import BaseScraper, ScrapeResult
from src.scrapers.browser_utils import REQUESTS_HEADERS, polite_delay
from src.utils import parse_price

logger = logging.getLogger(__name__)

OOS_TEXT = ["épuisé", "indisponible", "rupture de stock", "out of stock"]

CASE_RE   = re.compile(r'^(\d+)\s*[x×]\s*75\s*cl', re.IGNORECASE)
SINGLE_RE = re.compile(r'^75\s*cl', re.IGNORECASE)

FALLBACK_SELECTORS = [
    "span.price-item__unit",
    "[data-product-price]",
    "span.price",
    ".price",
    "span[itemprop='price']",
]


def extract_75cl_tiles(soup: BeautifulSoup) -> list[dict]:
    """
    Parse all format label tiles and return only 75cl ones.
    Each label looks like:
      <label>6 x 75cl<small>258,00€</small></label>
    """
    tiles = []

    for label in soup.find_all("label"):
        # Get label text excluding the <small> child
        small_el = label.find("small")
        if not small_el:
            continue

        price_text = small_el.get_text(strip=True)
        # Label text = full text minus the small price text
        label_text = label.get_text(strip=True).replace(price_text, "").strip()
        label_text = label_text.replace("\xa0", " ").strip()

        case_match   = CASE_RE.match(label_text)
        single_match = SINGLE_RE.match(label_text)

        if case_match:
            bottle_count = int(case_match.group(1))
        elif single_match:
            bottle_count = 1
        else:
            logger.debug(f"Cavissima: ignoring non-75cl tile '{label_text}'")
            continue

        try:
            total_price, currency = parse_price(price_text)
        except ValueError:
            logger.debug(f"Cavissima: could not parse tile price '{price_text}' for '{label_text}'")
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

        for vintage in vintages:
            try:
                if vintage and product.url_template:
                    url = product.url_template.format(vintage=vintage)
                else:
                    url = product.product_url
                if not url:
                    logger.warning(f"Cavissima: no URL for {product.estate_name} {vintage}")
                    continue

                r = requests.get(url, headers=REQUESTS_HEADERS, timeout=20)
                if r.status_code == 404:
                    continue
                r.raise_for_status()

                # Force UTF-8 to avoid binary decode errors
                r.encoding = "utf-8"
                soup = BeautifulSoup(r.text, "html.parser")

                tiles = extract_75cl_tiles(soup)

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
                    # Fallback to CSS selectors
                    logger.debug(f"Cavissima: no tiles found at {url}, trying CSS fallback")
                    selectors = []
                    if product.price_selector:
                        selectors.append(product.price_selector)
                    selectors.extend(FALLBACK_SELECTORS)

                    price_el = None
                    for sel in selectors:
                        price_el = soup.select_one(sel)
                        if price_el:
                            break

                    if not price_el:
                        logger.warning(f"Cavissima: no price found at {url}")
                        polite_delay(1.5, 3.0)
                        continue

                    raw_price = price_el.get("content") or price_el.get_text(strip=True)
                    if not raw_price:
                        polite_delay(1.5, 3.0)
                        continue

                    try:
                        price_amount, currency = parse_price(raw_price)
                        raw_price_text = raw_price
                    except ValueError as e:
                        logger.warning(f"Cavissima: could not parse '{raw_price}' at {url}: {e}")
                        polite_delay(1.5, 3.0)
                        continue

                # Availability
                availability = True
                page_text = soup.get_text(" ", strip=True).lower()
                for phrase in OOS_TEXT:
                    if phrase in page_text:
                        availability = False
                        break

                results.append(ScrapeResult(
                    vintage=vintage or 0,
                    price_amount=price_amount,
                    currency=currency,
                    raw_price_text=raw_price_text,
                    availability=availability,
                    url=url,
                    retailer=self.retailer,
                ))
                polite_delay(1.5, 3.0)

            except Exception as e:
                logger.warning(f"Cavissima: failed {product.estate_name} {vintage}: {e}")

        return results
