# src/scrapers/levindevantsoi.py
from src.scrapers.generic import GenericStaticScraper


class LeVinDeVantSoiScraper(GenericStaticScraper):
    retailer = "levindevantsoi"
    # p.price is the WooCommerce main product price container (unique per page)
    # Brotli-encoded responses handled automatically by the brotli package
    FALLBACK_SELECTORS = ["p.price"]
