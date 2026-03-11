"""
Base scraper interface. Every retailer scraper must implement this contract.

A scraper receives a MasterProduct and returns a list of ScrapeResult objects —
one per vintage found. The scraper_engine handles deduplication and DB persistence.
"""
from dataclasses import dataclass
from decimal import Decimal
from typing import Optional


@dataclass
class ScrapeResult:
    """Standardised result returned by every retailer scraper."""
    vintage: int                    # 0 = NV (non-vintage)
    price_amount: Optional[Decimal]
    currency: str
    raw_price_text: str
    availability: bool
    url: str
    retailer: str


class BaseScraper:
    """
    All retailer scrapers must inherit from this class and implement scrape().
    """
    retailer: str = ""

    def scrape(self, product) -> list[ScrapeResult]:
        """
        Scrape all relevant vintages for a given MasterProduct.
        Returns a list of ScrapeResult (one per vintage found).
        Must never raise — catch exceptions internally and return [] on failure.
        """
        raise NotImplementedError
