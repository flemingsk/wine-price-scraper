# src/scrapers/cave_spirituelle.py
from src.scrapers.generic import GenericStaticScraper


class CaveSpirituellesScraper(GenericStaticScraper):
    retailer = "cave_spirituelle"
    # span.price is unique per product page (PrestaShop, Brotli — handled by brotli pkg)
    FALLBACK_SELECTORS = ["span.price"]
