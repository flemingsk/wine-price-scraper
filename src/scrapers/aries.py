# src/scrapers/aries.py
from src.scrapers.generic import GenericStaticScraper


class AriesScraper(GenericStaticScraper):
    retailer = "aries"
    # Unit price: try to get the last span.font-weight-bold within product-unit-price
    # (case/unit price may both exist, unit is typically the one to use)
    FALLBACK_SELECTORS = [
        "span.product-unit-price > span.font-weight-bold:last-of-type",
        "span.product-unit-price span.font-weight-bold"
    ]
