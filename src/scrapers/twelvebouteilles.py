# src/scrapers/twelvebouteilles.py
from bs4 import BeautifulSoup
import requests
from src.scrapers.base import BaseScraper
from src.utils import parse_price

HEADERS = {"User-Agent": "Mozilla/5.0"}

class TwelveBouteillesScraper(BaseScraper):
    def scrape(self, vintage=None):
        # Build URL using template or fallback to product_url
        url = self.product.url_template.format(vintage=vintage) if self.product.url_template else self.product.product_url

        r = requests.get(url, headers=HEADERS, timeout=20)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")

        # Extract price
        price_el = soup.select_one(self.product.price_selector)
        raw_price = price_el.get_text(strip=True) if price_el else None
        price_amount, currency = parse_price(raw_price) if raw_price else (None, None)

        # Check availability
        availability = bool(price_el)

        return {
            "price_amount": price_amount,
            "currency": currency,
            "raw_price_text": raw_price,
            "availability": availability,
            "wine_color": "Rouge",  # Default for now
            "note": str(vintage) if vintage else None,
        }
