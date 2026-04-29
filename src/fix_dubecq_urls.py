"""
Fix Dubecq URLs in master_products.csv

Problem:
  CSV has old catalogue anchor links: https://www.dubecq.com/fr/3-catalogue#product-8302
  Should be direct product pages: https://www.dubecq.com/fr/catalogue/8302-chateau-latour-martillac-grand-cru-classe-2023-la-bouteille-75cl.html

Solution:
  1. Extract product ID from current URLs
  2. Scrape Dubecq catalogue page to get product details
  3. Reconstruct proper product page URL
  4. Validate product name/vintage matches CSV
  5. Update CSV with new URLs
"""

import re
import csv
from pathlib import Path
import requests
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright
from src.scrapers.browser_utils import REQUESTS_HEADERS, polite_delay

CSV_PATH = Path("master_products.csv")


def extract_product_id(url):
    """Extract product ID from anchor URL like #product-8302"""
    match = re.search(r'#product-(\d+)', url)
    return match.group(1) if match else None


def fetch_product_details(product_id, playwright_context=None):
    """
    Fetch product details from Dubecq product page directly.
    Try to construct URL based on product ID and fetch page details.
    Returns (product_url, product_title, price_text) or (None, None, None) if not found.
    """
    # Try direct access to product page using just the ID
    # Dubecq might have a direct product page accessible by ID alone
    test_urls = [
        f"https://www.dubecq.com/fr/catalogue/{product_id}",
        f"https://www.dubecq.com/fr/product/{product_id}",
        f"https://www.dubecq.com/fr/{product_id}",
    ]

    for test_url in test_urls:
        try:
            r = requests.get(test_url, headers=REQUESTS_HEADERS, timeout=20)
            if r.status_code == 200:
                soup = BeautifulSoup(r.text, "html.parser")

                # Check if we found a product page (should have our price selector)
                price_el = soup.find(id="our_price_display")
                if price_el:
                    price_text = price_el.get_text(strip=True)

                    # Extract product title
                    title_el = soup.find("h1") or soup.find("h2") or soup.find(class_=re.compile(r"product-name"))
                    product_title = title_el.get_text(strip=True) if title_el else None

                    print(f"  [OK] Found product at {test_url}")
                    return test_url, product_title, price_text
        except Exception as e:
            pass

    print(f"  [WARN] Could not find product page for ID {product_id}")
    return None, None, None


def reconstruct_url(product_id, name_slug):
    """Reconstruct proper Dubecq product URL"""
    if not name_slug:
        return None
    return f"https://www.dubecq.com/fr/catalogue/{product_id}-{name_slug}.html"


def verify_url_works(url):
    """Check if the reconstructed URL actually exists and has a price"""
    try:
        r = requests.get(url, headers=REQUESTS_HEADERS, timeout=20)
        if r.status_code == 404:
            return False, "404 Not Found"

        soup = BeautifulSoup(r.text, "html.parser")
        price_el = soup.find(class_=re.compile(r"price"))

        if not price_el:
            return False, "No price found on page"

        return True, price_el.get_text(strip=True)
    except Exception as e:
        return False, str(e)


def fix_dubecq_urls(dry_run=True):
    """
    Process all Dubecq entries in CSV and fix URLs.
    """
    # Read CSV
    with open(CSV_PATH, 'r', encoding='utf-8-sig') as f:
        reader = csv.DictReader(f)
        rows = list(reader)

    dubecq_rows = [r for r in rows if r.get('retailer') == 'dubecq']
    print(f"Found {len(dubecq_rows)} Dubecq entries to fix\n")

    updates = []

    for i, row in enumerate(dubecq_rows, 1):
        url = row['product_url']
        product_id = extract_product_id(url)

        if not product_id:
            print(f"{i}. [FAIL] Could not extract product ID from {url}")
            continue

        print(f"{i}. Product {product_id} | {row['estate_name']} {row['vintage_start']}")

        # Fetch product details from direct product page
        new_url, product_title, price_text = fetch_product_details(product_id)
        polite_delay(1.0, 2.0)

        if not new_url:
            print(f"   [FAIL] Could not fetch product from any URL format")
            continue

        print(f"   -> Product: {product_title}")
        print(f"   -> New URL: {new_url}")
        print(f"   -> Price: {price_text}")

        # Verify expected estate/vintage in title
        estate = row['estate_name'].lower()
        vintage = str(row['vintage_start'])

        if estate in product_title.lower() and vintage in product_title.lower():
            print(f"   [OK] Title matches estate and vintage")
            updates.append({
                'row_index': rows.index(row),
                'old_url': url,
                'new_url': new_url,
                'product_id': product_id,
                'estate': row['estate_name'],
                'vintage': row['vintage_start']
            })
        else:
            print(f"   [WARN] Title mismatch: expected {estate} {vintage} in '{product_title}'")

    print(f"\n{'='*80}")
    print(f"Found {len(updates)} URLs to update")

    if not updates:
        return

    # Show summary
    print("\nURLs to update:")
    for u in updates:
        print(f"  {u['estate']} {u['vintage']}: {u['product_id']}")

    if dry_run:
        print(f"\n[DRY RUN] Not modifying CSV. Run with --live to apply changes.")
        return

    # Apply updates
    print(f"\nUpdating CSV...")
    for update in updates:
        rows[update['row_index']]['product_url'] = update['new_url']

    # Write updated CSV
    with open(CSV_PATH, 'w', newline='', encoding='utf-8-sig') as f:
        writer = csv.DictWriter(f, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)

    print(f"[OK] Updated {len(updates)} rows in master_products.csv")


if __name__ == "__main__":
    import sys
    dry_run = "--live" not in sys.argv
    fix_dubecq_urls(dry_run=dry_run)
