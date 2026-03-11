from src.scrapers.millesima import MillesimaScraper
from src.scrapers.vinatis import VinatisScraper
from src.scrapers.jean_merlaut import JeanMerlautScraper
from src.scrapers.idealwine import IdealwineScraper
from src.scrapers.lavignery import LaVigneryScraper, VinodisScraper
from src.scrapers.twil import TwilScraper
from src.scrapers.cavissima import CavissimaScraper
from src.scrapers.wine_searcher import WineSearcherScraper

SCRAPERS = {
    "millesima":     MillesimaScraper,
    "vinatis":       VinatisScraper,
    "jean_merlaut":  JeanMerlautScraper,
    "idealwine":     IdealwineScraper,
    "lavignery":     LaVigneryScraper,
    "vinodis":       VinodisScraper,
    "twil":          TwilScraper,
    "12bouteilles":  TwilScraper,
    "cavissima":     CavissimaScraper,
    "wine-searcher": WineSearcherScraper,
}
