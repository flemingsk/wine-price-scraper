# src/scrapers/chateaunet.py
from src.scrapers.generic import GenericStaticScraper


class ChateaunetScraper(GenericStaticScraper):
    retailer = "chateaunet"
    # Price is in itemprop="price" content attribute — clean decimal, no parsing issues
    # e.g. <span itemprop="price" content="60.27">60,27 €</span>
    FALLBACK_SELECTORS = [
        "span[itemprop='price']",
        "#product-price",
        "span.price",
    ]
