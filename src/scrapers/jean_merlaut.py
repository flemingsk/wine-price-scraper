# src/scrapers/jean_merlaut.py
from bs4 import BeautifulSoup
import requests
from src.scrapers.base import BaseScraper
from src.utils import parse_price

HEADERS = {"User-Agent": "Mozilla/5.0"}

class JeanMerlautScraper(BaseScraper):
    def scrape(self, product, vintage=None):
        url = product.url_template.format(vintage=vintage) if (vintage and product.url_template) else product.product_url
        r = requests.get(url, headers=HEADERS, timeout=20)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")

        price_el = soup.select_one(product.price_selector)
        raw_price = price_el.get_text(strip=True) if price_el else None
        price_amount, currency = parse_price(raw_price) if raw_price else (None, None)

        return {
            "price_amount": price_amount,
            "currency": currency,
            "raw_price_text": raw_price,
            "availability": bool(price_el),
            "wine_color": "Rouge",
            "note": str(vintage) if vintage else None,
        }
