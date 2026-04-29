"""
Test Dubecq scraper with new URLs to verify price extraction works correctly.
"""

from src.db import SessionLocal
from src.models import MasterProduct
from src.scrapers.registry import get_scraper
from src.scrapers.browser_utils import polite_delay

db = SessionLocal()

# Get all Dubecq products
dubecq_products = (
    db.query(MasterProduct)
    .filter(MasterProduct.retailer == "dubecq")
    .order_by(MasterProduct.estate_name, MasterProduct.vintage_start)
    .all()
)

print(f"Testing {len(dubecq_products)} Dubecq products\n")
print(f"{'Estate':<40} {'Vintage':<10} {'Price':<15} {'Status'}")
print("-" * 80)

scraper = get_scraper("dubecq")
results = []

for product in dubecq_products:
    try:
        scraped = scraper.scrape(product)

        if scraped:
            for result in scraped:
                price_str = f"{result.price_amount} {result.currency}" if result.price_amount else "N/A"
                status = "OK"
                results.append({
                    'estate': product.estate_name,
                    'vintage': result.vintage,
                    'price': price_str,
                    'status': status,
                    'url': result.url
                })
                print(f"{product.estate_name:<40} {result.vintage:<10} {price_str:<15} {status}")
        else:
            print(f"{product.estate_name:<40} {product.vintage_start:<10} {'N/A':<15} NO RESULTS")
            results.append({
                'estate': product.estate_name,
                'vintage': product.vintage_start,
                'price': 'N/A',
                'status': 'NO RESULTS',
                'url': product.product_url
            })

        polite_delay(1.0, 2.0)

    except Exception as e:
        print(f"{product.estate_name:<40} {product.vintage_start:<10} {'ERROR':<15} {str(e)[:40]}")
        results.append({
            'estate': product.estate_name,
            'vintage': product.vintage_start,
            'price': 'ERROR',
            'status': str(e)[:40],
            'url': product.product_url
        })

print("\n" + "=" * 80)
print(f"\nSummary:")
ok_count = len([r for r in results if r['status'] == 'OK'])
error_count = len([r for r in results if r['status'] not in ('OK', 'NO RESULTS')])
no_results = len([r for r in results if r['status'] == 'NO RESULTS'])

print(f"  Successful: {ok_count}/{len(results)}")
print(f"  No results: {no_results}/{len(results)}")
print(f"  Errors: {error_count}/{len(results)}")

db.close()
