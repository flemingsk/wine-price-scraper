"""
Wine-Searcher scraper

Wine-Searcher has a FREE API tier (100 calls/day) that returns:
  - min/avg/max price across all merchants
  - critic score (aggregated)
  - availability across merchants

This is the highest-value source to add: one call per wine name + vintage
returns prices aggregated from dozens of French and international merchants.

API docs: https://www.wine-searcher.com/trade/ws-api

Required env var: WINE_SEARCHER_API_KEY
Free trial: contact wine-searcher.com for a key (100 free calls/day)

API call format:
  GET https://www.wine-searcher.com/api.lml?
      api_key={key}
      &winename={name}
      &vintage={year}
      &location=fr          # France — gets EUR prices
      &format=json
      &num_results=1

Returns: price-min, price-average, price-max in EUR
"""
import logging
import os
import time
import re

import requests

from src.scrapers.base import BaseScraper, ScrapeResult
from src.scrapers.browser_utils import polite_delay
from src.utils import parse_price
from decimal import Decimal

logger = logging.getLogger(__name__)

WINE_SEARCHER_BASE = "https://www.wine-searcher.com/api.lml"


class WineSearcherScraper(BaseScraper):
    """
    Uses the Wine-Searcher API to get aggregated market prices.
    Returns the minimum price found across all merchants in France.
    Set WINE_SEARCHER_API_KEY environment variable to enable.
    """
    retailer = "wine-searcher"

    def scrape(self, product) -> list[ScrapeResult]:
        api_key = os.getenv("WINE_SEARCHER_API_KEY")
        if not api_key:
            logger.warning("WINE_SEARCHER_API_KEY not set — skipping Wine-Searcher")
            return []

        results = []

        if product.vintage_start and product.vintage_end:
            vintages = range(product.vintage_start, product.vintage_end + 1)
        else:
            vintages = [None]

        for vintage in vintages:
            try:
                result = self._query_api(api_key, product.estate_name, vintage)
                if result:
                    results.append(result)
                polite_delay(1.5, 3.0)  # API rate limiting

            except Exception as e:
                logger.warning(f"Wine-Searcher: failed {product.estate_name} {vintage}: {e}")
                continue

        return results

    def _query_api(self, api_key: str, wine_name: str, vintage: int | None) -> ScrapeResult | None:
        params = {
            "api_key": api_key,
            "winename": wine_name,
            "location": "fr",        # France — EUR prices
            "format": "json",
            "num_results": "1",
        }

        if vintage:
            params["vintage"] = str(vintage)

        url = f"{WINE_SEARCHER_BASE}?{_build_query(params)}"

        r = requests.get(
            WINE_SEARCHER_BASE,
            params=params,
            timeout=15,
            headers={"User-Agent": "WinePriceMonitor/1.0"},
        )
        r.raise_for_status()

        data = r.json()

        # Wine-Searcher returns status 0 = success
        if data.get("status") != 0 and data.get("Status") != "0":
            status = data.get("status") or data.get("Status")
            logger.info(f"Wine-Searcher: no result for '{wine_name}' {vintage} (status {status})")
            return None

        # Extract price — try min price first
        price_min = self._extract_price(data, "price-min") or self._extract_price(data, "price-average")
        if price_min is None:
            return None

        price_avg = self._extract_price(data, "price-average") or price_min

        detected_vintage = vintage or 0

        # Build a descriptive URL for the record
        name_slug = wine_name.replace(" ", "+")
        record_url = f"https://www.wine-searcher.com/find/{name_slug}/{detected_vintage}/fr"

        return ScrapeResult(
            vintage=detected_vintage,
            price_amount=price_min,
            currency="EUR",
            raw_price_text=f"min: {price_min} EUR (avg: {price_avg} EUR)",
            availability=True,
            url=record_url,
            retailer=self.retailer,
        )

    def _extract_price(self, data: dict, field: str) -> Decimal | None:
        value = data.get(field) or data.get(field.replace("-", "_"))
        if value is None:
            return None
        try:
            return Decimal(str(value))
        except Exception:
            return None


def _build_query(params: dict) -> str:
    """Simple URL query string builder."""
    return "&".join(f"{k}={v}" for k, v in params.items())
