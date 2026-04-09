# src/scrapers/millesimes.py  (millesimes.com — distinct from millesima.fr)
from src.scrapers.generic import GenericStaticScraper


class MillesimesScraper(GenericStaticScraper):
    retailer = "millesimes"
    # span.price-current is unique per product page
    FALLBACK_SELECTORS = ["span.price-current"]
