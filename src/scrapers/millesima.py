"""
Millesima scraper (millesima.fr)

All format prices are embedded in static HTML as Tile components.
No JavaScript / Playwright needed.

Strategy:
  1. Find all Tile_text__ containers
  2. Read label from Tile_paragraph__ and price from Tile_sub-paragraph__
  3. Keep only 75cl tiles (single bottle or cases: "75CL", "6 x 75CL", "12 x 75CL")
  4. Pick largest case (12 > 6 > 1) for best unit price
  5. Divide tile total price by bottle count → unit price
  6. Fall back to regex on full page text if no tiles found
"""
import logging
import re
from decimal import Decimal, InvalidOperation

import requests
from bs4 import BeautifulSoup

from src.scrapers.base import BaseScraper, ScrapeResult
from src.scrapers.browser_utils import REQUESTS_HEADERS, polite_delay
from src.utils import parse_price

logger = logging.getLogger(__name__)

OOS_TEXT = ["épuisé", "indisponible", "rupture de stock", "out of stock"]

# "6 x 75CL", "12 x 75CL", "1 x 75CL"
CASE_RE   = re.compile(r'^(\d+)\s*[x×]\s*75\s*cl$', re.IGNORECASE)
# bare "75CL" (single bottle, no quantity prefix)
SINGLE_RE = re.compile(r'^75\s*cl$', re.IGNORECASE)


def extract_75cl_tiles(soup: BeautifulSoup) -> list[dict]:
    """
    Parse all Tile containers and return only 75cl ones.
    Each result: {label, bottle_count, total_price, unit_price, currency}
    """
    tiles = []

    for container in soup.find_all(class_=re.compile(r'Tile_text__')):
        label_el = container.find(class_=re.compile(r'Tile_paragraph__'))
        price_el = container.find(class_=re.compile(r'Tile_sub-paragraph__'))
        if not label_el or not price_el:
            continue

        label      = label_el.get_text(separator=" ", strip=True).replace("\xa0", " ").strip()
        price_text = price_el.get_text(strip=True)

        # Determine bottle count — skip non-75cl tiles silently
        case_match = CASE_RE.match(label)
        if case_match:
            bottle_count = int(case_match.group(1))
        elif SINGLE_RE.match(label):
            bottle_count = 1
        else:
            logger.debug(f"Millesima: ignoring non-75cl tile '{label}'")
            continue

        try:
            total_price, currency = parse_price(price_text)
        except ValueError:
            logger.debug(f"Millesima: could not parse tile price '{price_text}' for '{label}'")
            continue

        unit_price = round(float(total_price) / bottle_count, 2)

        tiles.append({
            "label":        label,
            "bottle_count": bottle_count,
            "total_price":  float(total_price),
            "unit_price":   unit_price,
            "currency":     currency,
        })

    return tiles


class MillesimaScraper(BaseScraper):
    retailer = "millesima"

    def scrape(self, product) -> list[ScrapeResult]:
        results = []

        if not product.url_template:
            logger.warning(f"Millesima: {product.estate_name} has no url_template")
            return results

        vintages = (
            range(product.vintage_start, product.vintage_end + 1)
            if product.vintage_start and product.vintage_end
            else [None]
        )

        for vintage in vintages:
            try:
                url = product.url_template.format(vintage=vintage) if vintage else product.url_template
                result = self._scrape_single(url, vintage or 0, product)
                if result:
                    results.append(result)
                polite_delay(2.0, 4.0)
            except Exception as e:
                logger.warning(f"Millesima: failed {product.estate_name} {vintage}: {e}")

        return results

    def _scrape_single(self, url: str, vintage: int, product) -> ScrapeResult | None:
        r = requests.get(url, headers=REQUESTS_HEADERS, timeout=20)
        if r.status_code == 404:
            return None
        r.raise_for_status()

        soup = BeautifulSoup(r.text, "html.parser")

        # --- Parse 75cl tiles ---
        tiles = extract_75cl_tiles(soup)

        if tiles:
            for t in tiles:
                logger.debug(
                    f"Millesima: [{product.estate_name} {vintage}] "
                    f"'{t['label']}' total={t['total_price']:.2f} "
                    f"→ unit={t['unit_price']:.2f} {t['currency']}"
                )

            # Prefer largest case (12 > 6 > 1)
            best = max(tiles, key=lambda t: t["bottle_count"])

            price_amount   = best["unit_price"]
            currency       = best["currency"]
            raw_price_text = (
                f"{best['label']} @ {best['total_price']:.2f} {currency} "
                f"= {price_amount:.2f} {currency}/bottle"
            )
            logger.info(
                f"Millesima: {product.estate_name} {vintage} — "
                f"'{best['label']}' → unit price {price_amount:.2f} {currency}"
            )

        else:
            # --- Regex fallback if no tiles found ---
            logger.debug(f"Millesima: no 75cl tiles found at {url}, trying regex fallback")
            match = re.search(r'(\d{1,4}[.,]\d{2})\s*(?:€|EUR)', soup.get_text(" ", strip=True))
            if not match:
                logger.warning(f"Millesima: no price found at {url}")
                return None

            raw_price_text = match.group(0)
            logger.info(f"Millesima: regex fallback '{raw_price_text}' at {url}")
            try:
                price_amount, currency = parse_price(raw_price_text)
                price_amount = float(price_amount)
            except ValueError:
                logger.warning(f"Millesima: could not parse '{raw_price_text}' at {url}")
                return None

        # --- Availability ---
        availability = True
        page_text = soup.get_text(" ", strip=True).lower()
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