"""
Scraper registry — maps retailer name strings to scraper classes.

To add a new retailer:
1. Create src/scrapers/myretailer.py with a class MyRetailerScraper(BaseScraper)
2. Add an entry to REGISTRY below

The retailer name must match exactly what's stored in MasterProduct.retailer.
"""
from src.scrapers.base import BaseScraper
from src.scrapers.vinatis import VinatisScraper
from src.scrapers.millesima import MillesimaScraper
from src.scrapers.idealwine import IdealwineScraper
from src.scrapers.cavissima import CavissimaScraper
from src.scrapers.lavignery import LaVigneryScraper, VinodisScraper
from src.scrapers.wine_searcher import WineSearcherScraper
from src.scrapers.jean_merlaut import JeanMerlautScraper
from src.scrapers.twil import TwilScraper

REGISTRY: dict[str, type[BaseScraper]] = {
    "vinatis":        VinatisScraper,
    "millesima":      MillesimaScraper,
    "idealwine":      IdealwineScraper,
    "cavissima":      CavissimaScraper,
    "lavignery":      LaVigneryScraper,
    "vinodis":        VinodisScraper,
    "wine-searcher":  WineSearcherScraper,
    "jean_merlaut":   JeanMerlautScraper,
    "12bouteilles":   TwilScraper,   # placeholder — see note below
    "twil":           TwilScraper,
}


def get_scraper(retailer: str) -> BaseScraper:
    """
    Return an instantiated scraper for the given retailer name.
    Raises ValueError if retailer is not registered.
    """
    key = retailer.lower().strip()
    cls = REGISTRY.get(key)
    if cls is None:
        raise ValueError(
            f"No scraper registered for retailer '{retailer}'. "
            f"Available: {sorted(REGISTRY.keys())}"
        )
    return cls()
