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
from src.scrapers.xovin import XovinScraper
from src.scrapers.hallesdequercamps import HallesDeQuercampsScraper
from src.scrapers.vintageandco import VintageAndCoScraper
from src.scrapers.cave_spirituelle import CaveSpirituellesScraper
from src.scrapers.chateauinternet import ChateauInternetScraper
from src.scrapers.cashvin import CashvinScraper
from src.scrapers.millesimes import MillesimesScraper
from src.scrapers.levindevantsoi import LeVinDeVantSoiScraper
from src.scrapers.labouteilledoree import LaBouteilleDoreeeScraper
from src.scrapers.vinotheque_bordeaux import VinothequeBordeauxScraper
from src.scrapers.vin_malin import VinMalinScraper
from src.scrapers.cercledemartillac import CercleDeMartillacScraper

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
    "xovin":         XovinScraper,
    "hallesdequercamps": HallesDeQuercampsScraper,
    "vintageandco":      VintageAndCoScraper,
    "cave_spirituelle":  CaveSpirituellesScraper,
    "chateauinternet":   ChateauInternetScraper,
    "cashvin":           CashvinScraper,
    "millesimes":        MillesimesScraper,
    "levindevantsoi":    LeVinDeVantSoiScraper,
    "labouteilledoree":  LaBouteilleDoreeeScraper,
    "vinotheque_bordeaux": VinothequeBordeauxScraper,
    "vin_malin":           VinMalinScraper,
    "cercledemartillac":   CercleDeMartillacScraper,
}
