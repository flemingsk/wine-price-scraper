# src/scrapers/vinotheque_bordeaux.py
from src.scrapers.generic import GenericStaticScraper


class VinothequeBordeauxScraper(GenericStaticScraper):
    retailer = "vinotheque_bordeaux"
    # vinotheque silently redirects unavailable products to a category page (soft-404),
    # returning HTTP 200 with a phantom price. Skip if the response URL path differs.
    verify_url_match = True
