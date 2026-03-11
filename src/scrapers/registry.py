from src.scrapers.base import BaseScraper
from src.scrapers.millesima import MillesimaScraper
from src.scrapers.vinatis import VinatisScraper
from src.scrapers.jean_merlaut import JeanMerlautScraper
from src.scrapers.idealwine import IdealwineScraper
from src.scrapers.lavignery import LaVigneryScraper, VinodisScraper
from src.scrapers.twil import TwilScraper
from src.scrapers.twelvebouteilles import TwelveBouteillesScraper
from src.scrapers.cavissima import CavissimaScraper
from src.scrapers.wine_searcher import WineSearcherScraper

REGISTRY: dict[str, type[BaseScraper]] = {
    "millesima":     MillesimaScraper,
    "vinatis":       VinatisScraper,
    "jean_merlaut":  JeanMerlautScraper,
    "idealwine":     IdealwineScraper,
    "lavignery":     LaVigneryScraper,
    "vinodis":       VinodisScraper,
    "twil":          TwilScraper,
    "12bouteilles":  TwelveBouteillesScraper,
    "cavissima":     CavissimaScraper,
    "wine-searcher": WineSearcherScraper,
}


def get_scraper(retailer: str) -> BaseScraper:
    key = retailer.lower().strip()
    cls = REGISTRY.get(key)
    if cls is None:
        raise ValueError(
            f"No scraper registered for retailer '{retailer}'. "
            f"Available: {sorted(REGISTRY.keys())}"
        )
    return cls()
