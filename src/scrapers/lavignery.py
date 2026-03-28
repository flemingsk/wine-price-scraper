# src/scrapers/lavignery.py
from src.scrapers.generic import GenericStaticScraper


class LaVigneryScraper(GenericStaticScraper):
    retailer = "lavignery"


class VinodisScraper(GenericStaticScraper):
    retailer = "vinodis"
