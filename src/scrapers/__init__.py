from src.scrapers.vinatis import VinatisScraper
from src.scrapers.jean_merlaut import JeanMerlautScraper
# Add other scrapers here

SCRAPERS = {
    "millesima": MillesimaScraper,
    "vinatis": VinatisScraper,
    "jean-merlaut": JeanMerlautScraper,
    "idealwine": IdealwineScraper,
    "lavignery": LaVigneryScraper,
    "twil": TwilScraper,
    "cavissima": CavissimaScraper,
    # ...
}
