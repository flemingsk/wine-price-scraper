# src/scrapers/__init__.py
from src.scrapers.millesima import MillesimaScraper
from src.scrapers.vinatis import VinatisScraper
from src.scrapers.idealwine import IdealwineScraper
from src.scrapers.wine_searcher import WineSearcherScraper
from src.scrapers.jean_merlaut import JeanMerlautScraper
from src.scrapers.twelvebouteilles import TwelveBouteillesScraper
from src.scrapers.cavissima import CavissimaScraper
from src.scrapers.lavignery import LaVigneryScraper, VinodisScraper
from src.scrapers.twil import TwilScraper
from src.scrapers.chateaunet import ChateaunetScraper
from src.scrapers.wineandco import WineandcoScraper
from src.scrapers.aries import AriesScraper
from src.scrapers.wineclub import WineclubScraper
from src.scrapers.dubecq import DubecqScraper

SCRAPERS = {
    # Custom scrapers
    "millesima":     MillesimaScraper,
    "vinatis":       VinatisScraper,
    "idealwine":     IdealwineScraper,
    "wine-searcher": WineSearcherScraper,
    # Generic static scrapers
    "jean_merlaut":  JeanMerlautScraper,
    "12bouteilles":  TwelveBouteillesScraper,
    "cavissima":     CavissimaScraper,
    "lavignery":     LaVigneryScraper,
    "vinodis":       VinodisScraper,
    "twil":          TwilScraper,
    "chateaunet":    ChateaunetScraper,
    "wineandco":     WineandcoScraper,
    "aries":         AriesScraper,
    "wineclub":      WineclubScraper,
    "dubecq":        DubecqScraper,
}
