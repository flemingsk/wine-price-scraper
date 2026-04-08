# src/scrapers/hallesdequercamps.py
from src.scrapers.generic import GenericStaticScraper


class HallesDeQuercampsScraper(GenericStaticScraper):
    retailer = "hallesdequercamps"
    # span[itemprop='price'] is unique per page and exposes the price
    # via the HTML content attribute (e.g. content="41.5")
    FALLBACK_SELECTORS = ["span[itemprop='price']"]
