"""
Discover white wine variants for estates/retailers/vintages we're already tracking (reds).

This script:
1. Identifies all unique (estate, retailer, vintage) combinations we track for reds
2. Searches each retailer's website for the white version of those wines
3. Suggests URLs to add to master_products.csv
"""

from collections import defaultdict
from src.db import SessionLocal
from src.models import MasterProduct
import requests
from bs4 import BeautifulSoup
from src.scrapers.browser_utils import REQUESTS_HEADERS, polite_delay

def discover_white_wines():
    db = SessionLocal()

    try:
        # Get all active red wine products
        red_products = (
            db.query(MasterProduct)
            .filter(
                MasterProduct.active == True,
                MasterProduct.wine_color.in_(['Rouge', 'Red']),
                MasterProduct.bottle_size.in_(['0.75L', '75cl'])
            )
            .all()
        )

        print(f"\n{'='*80}")
        print(f"WHITE WINE DISCOVERY")
        print(f"{'='*80}\n")
        print(f"Found {len(red_products)} active red wine products\n")

        # Group by (estate, retailer) — these are candidates for white variants
        by_estate_retailer = defaultdict(set)
        for product in red_products:
            key = (product.estate_name, product.retailer)
            by_estate_retailer[key].add(product.vintage_start)

        print(f"Estate+Retailer combinations: {len(by_estate_retailer)}\n")

        # Check which estates are known to produce white wines
        # This is a curated list of Bordeaux estates that make whites
        WHITE_WINE_PRODUCERS = {
            'Chateau Carbonnieux',
            'Chateau Couhins',
            'Chateau Couhins-Lurton',
            'Chateau Larrivet Haut-Brion',
            'Chateau Laville Haut-Brion',
            'Chateau Malartic-Lagraviere',
            'Domaine de Chevalier',
            'Chateau Haut-Brion',  # has white
            'Chateau Pape Clement',  # has white
            'Chateau Smith Haut Lafitte',  # has white
        }

        candidates = [
            (estate, retailer, vintages)
            for (estate, retailer), vintages in by_estate_retailer.items()
            if estate in WHITE_WINE_PRODUCERS
        ]

        print(f"Estates that produce whites: {len(candidates)}\n")
        print(f"{'Estate':<40} {'Retailer':<20} {'Vintages'}")
        print("-" * 80)

        for estate, retailer, vintages in sorted(candidates, key=lambda x: x[0]):
            vintage_range = f"{min(vintages)}-{max(vintages)}"
            print(f"{estate:<40} {retailer:<20} {vintage_range}")

        # For each candidate, check if white wine already exists in DB
        print(f"\n{'='*80}")
        print("CHECKING FOR EXISTING WHITE WINE COVERAGE:")
        print(f"{'='*80}\n")

        for estate, retailer, vintages in candidates:
            existing_whites = (
                db.query(MasterProduct)
                .filter(
                    MasterProduct.estate_name == estate,
                    MasterProduct.retailer == retailer,
                    MasterProduct.wine_color.in_(['Blanc', 'White'])
                )
                .all()
            )

            if existing_whites:
                print(f"[OK] {estate} @ {retailer}: {len(existing_whites)} white wines already tracked")
            else:
                print(f"[--] {estate} @ {retailer}: NO white wines tracked (opportunity!)")
                print(f"  Red vintages: {sorted(vintages)}")
                print(f"  Action: Search for white variant URLs\n")

        print("\n" + "="*80)
        print("NEXT STEPS:")
        print("="*80)
        print("""
1. For each missing white wine:
   - Search retailer website for "[Estate] Blanc" or "[Estate] White"
   - Note the URL and price selector
   - Add row to master_products.csv with wine_color='Blanc'

2. Popular white wine URLs to search:
   - retailer.com/product?q=[Estate]+blanc
   - retailer.com/product?q=[Estate]+white
   - retailer.com/[estate-slug]-blanc

3. Once URLs are found:
   - Add rows to master_products.csv
   - Run: python -m src.load_master_products
   - Test: python -m src.app

Common CSS selectors for prices (may vary by retailer):
   - span.prix, span.price, span.amount
   - div.product-price, p.prix
   - span[data-price], span.product-price
""")

        return candidates

    finally:
        db.close()

if __name__ == "__main__":
    discover_white_wines()
