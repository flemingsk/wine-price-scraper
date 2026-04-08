# src/scrapers/aries.py
from src.scrapers.generic import GenericStaticScraper


class AriesScraper(GenericStaticScraper):
    retailer = "aries"
    # Case price is in span.price-first.font-weight-bold — unit price is one level deeper
    FALLBACK_SELECTORS = ["span.product-unit-price span.font-weight-bold"]
