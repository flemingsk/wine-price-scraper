from src.scrapers.base import BaseScraper
from src.scrapers.vinatis_logic import scrape_vinatis_price


class VinatisScraper(BaseScraper):
    def scrape(self, url, product):
        return scrape_vinatis_price(url, product.price_selector)
