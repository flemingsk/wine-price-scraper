"""
Fix Dubecq URLs in master_products.csv using product ID to URL mapping.

Pattern: https://www.dubecq.com/fr/catalogue/{product_id}-{slug}.html

Where slug is: {estate-slug}-{designation}-{vintage}-la-bouteille-75cl

Designations by estate:
- Carbonnieux: grand-cru-classe
- Larrivet Haut-Brion: rouge
- Malartic-Lagraviere: grand-cru-classe
- Sociando-Mallet: rouge
"""

import re
import csv
from pathlib import Path

CSV_PATH = Path("master_products.csv")

# Map estate names to their URL slug designation
ESTATE_SLUGS = {
    "Chateau Carbonnieux": ("chateau-carbonnieux", "grand-cru-classe"),
    "Chateau Larrivet Haut-Brion": ("chateau-larrivet-haut-brion", "rouge"),
    "Chateau Latour Martillac": ("chateau-latour-martillac", "grand-cru-classe"),
    "Chateau Malartic-Lagraviere": ("chateau-malartic-lagraviere", "grand-cru-classe"),
    "Chateau Sociando-Mallet": ("chateau-sociando-mallet", "rouge"),
}


def extract_product_id(url):
    """Extract product ID from anchor URL like #product-8302"""
    match = re.search(r'#product-(\d+)', url)
    return match.group(1) if match else None


def build_new_url(product_id, estate_name, vintage):
    """Build proper Dubecq product URL"""
    if estate_name not in ESTATE_SLUGS:
        return None

    estate_slug, designation = ESTATE_SLUGS[estate_name]
    return f"https://www.dubecq.com/fr/catalogue/{product_id}-{estate_slug}-{designation}-{vintage}-la-bouteille-75cl.html"


def fix_dubecq_urls(dry_run=True):
    """Update all Dubecq URLs in CSV"""
    with open(CSV_PATH, 'r', encoding='utf-8-sig') as f:
        reader = csv.DictReader(f)
        rows = list(reader)

    dubecq_rows = [(i, r) for i, r in enumerate(rows) if r.get('retailer') == 'dubecq']
    print(f"Found {len(dubecq_rows)} Dubecq entries to fix\n")

    updates = []

    for idx, (row_idx, row) in enumerate(dubecq_rows, 1):
        url = row['product_url']
        product_id = extract_product_id(url)
        estate = row['estate_name']
        vintage = row['vintage_start']

        if not product_id:
            print(f"{idx}. [FAIL] Could not extract product ID from {url}")
            continue

        if estate not in ESTATE_SLUGS:
            print(f"{idx}. [FAIL] Unknown estate: {estate}")
            continue

        new_url = build_new_url(product_id, estate, vintage)

        print(f"{idx}. {estate} {vintage}")
        print(f"   ID: {product_id}")
        print(f"   -> {new_url}")

        updates.append({
            'row_idx': row_idx,
            'old_url': url,
            'new_url': new_url,
            'estate': estate,
            'vintage': vintage,
        })

    print(f"\n{'='*80}")
    print(f"Found {len(updates)} URLs to update")

    if not updates:
        return

    if dry_run:
        print(f"\n[DRY RUN] Not modifying CSV. Run with --live to apply changes.")
        return

    # Apply updates
    print(f"\nUpdating CSV...")
    for update in updates:
        rows[update['row_idx']]['product_url'] = update['new_url']

    # Write to backup first, then replace
    backup_path = CSV_PATH.with_suffix('.backup.csv')
    with open(backup_path, 'w', newline='', encoding='utf-8-sig') as f:
        writer = csv.DictWriter(f, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)

    # Replace original with backup
    import shutil
    shutil.move(str(backup_path), str(CSV_PATH))

    print(f"[OK] Updated {len(updates)} rows in master_products.csv")


if __name__ == "__main__":
    import sys
    dry_run = "--live" not in sys.argv
    fix_dubecq_urls(dry_run=dry_run)
