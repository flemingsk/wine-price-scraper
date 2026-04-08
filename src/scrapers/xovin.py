# src/scrapers/xovin.py
from src.scrapers.generic import GenericStaticScraper


class XovinScraper(GenericStaticScraper):
    retailer = "xovin"
    # span.product-price.format-price-xovin is unique per page and exposes
    # the price via the HTML content attribute (e.g. content="45")
    FALLBACK_SELECTORS = ["span.product-price.format-price-xovin"]
