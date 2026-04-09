# src/scrapers/chateauinternet.py
from src.scrapers.generic import GenericStaticScraper


class ChateauInternetScraper(GenericStaticScraper):
    retailer = "chateauinternet"
    # div.price is unique per product page
    FALLBACK_SELECTORS = ["div.price"]
