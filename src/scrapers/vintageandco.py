# src/scrapers/vintageandco.py
from src.scrapers.generic import GenericStaticScraper


class VintageAndCoScraper(GenericStaticScraper):
    retailer = "vintageandco"
    # span.current-price-value is unique per page, exposes price via content attribute
    FALLBACK_SELECTORS = ["span.current-price-value"]
