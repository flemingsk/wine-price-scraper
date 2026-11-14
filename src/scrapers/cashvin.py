# src/scrapers/cashvin.py
from src.scrapers.generic import GenericStaticScraper


class CashvinScraper(GenericStaticScraper):
    retailer = "cashvin"
    # p.price is the WooCommerce main product price container (unique per page)
    # parse_price handles the "39,00€/ 75 cl TTC" format correctly
    FALLBACK_SELECTORS = ["p.price"]
    # Delisted/renamed products can redirect to an unrelated page (home, category)
    # where p.price matches a different product entirely — reject silently.
    verify_url_match = True
