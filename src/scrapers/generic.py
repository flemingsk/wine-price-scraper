import requests
from bs4 import BeautifulSoup

from src.scrapers.base import BaseScraper
from src.utils import parse_price


HEADERS = {
    "User-Agent": "Mozilla/5.0"
}


class GenericScraper(BaseScraper):
    retailer = "generic"

    def scrape(self, url: str, price_selector: str):
        r = requests.get(url, headers=HEADERS, timeout=20)

        if r.status_code == 404:
            return None, None, None, False

        r.raise_for_status()

        soup = BeautifulSoup(r.text, "html.parser")
        price_el = soup.select_one(price_selector)

        if not price_el:
            return None, None, None, False

        raw_price = price_el.get_text(strip=True)
        price_amount, currency = parse_price(raw_price)

        return price_amount, currency, raw_price, True
