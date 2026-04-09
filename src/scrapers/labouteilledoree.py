# src/scrapers/labouteilledoree.py
import logging

from src.scrapers.base import ScrapeResult
from src.scrapers.generic import GenericStaticScraper

logger = logging.getLogger(__name__)


class LaBouteilleDoreeeScraper(GenericStaticScraper):
    retailer = "labouteilledoree"
    # div.current-price is the PrestaShop price element.
    # Product URLs are 6-bottle wooden cases — divide scraped price by 6.
    FALLBACK_SELECTORS = ["div.current-price"]
    _CASE_BOTTLES = 6

    def scrape(self, product) -> list[ScrapeResult]:
        results = super().scrape(product)
        adjusted = []
        for r in results:
            if r.price_amount is not None:
                unit_price = round(r.price_amount / self._CASE_BOTTLES, 2)
                logger.info(
                    f"{self.retailer}: {product.estate_name} {r.vintage} — "
                    f"case {r.price_amount} ÷ {self._CASE_BOTTLES} = {unit_price} {r.currency}"
                )
                adjusted.append(ScrapeResult(
                    vintage=r.vintage,
                    price_amount=unit_price,
                    currency=r.currency,
                    raw_price_text=f"[case/{self._CASE_BOTTLES}] {r.raw_price_text}",
                    availability=r.availability,
                    url=r.url,
                    retailer=r.retailer,
                ))
            else:
                adjusted.append(r)
        return adjusted
