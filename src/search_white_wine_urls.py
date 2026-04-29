"""
Search retailer websites for white wine variants of tracked estates.

Produces a list of (estate, retailer, vintage, url, price_selector) rows
ready to be appended to master_products.csv.

Currently targets: Chateau Carbonnieux Blanc, Chateau Malartic-Lagraviere Blanc,
                   Chateau Larrivet Haut-Brion Blanc
"""

import re
import time
import requests
from bs4 import BeautifulSoup
from src.scrapers.browser_utils import REQUESTS_HEADERS, polite_delay

# Estates to search for + their Blanc search terms
WHITE_ESTATES = {
    "Chateau Carbonnieux":       ["carbonnieux blanc", "carbonnieux white"],
    "Chateau Malartic-Lagraviere": ["malartic blanc", "malartic lagraviere blanc"],
    "Chateau Larrivet Haut-Brion": ["larrivet haut brion blanc", "larrivet blanc"],
}

# Vintages to look for (same range as existing reds)
TARGET_VINTAGES = list(range(2015, 2025))

def search_millesima(estate_key, search_term):
    """Search Millesima for white wine variants."""
    results = []
    url = f"https://www.millesima.fr/search?q={search_term.replace(' ', '+')}&wine_color=blanc"
    try:
        r = requests.get(url, headers=REQUESTS_HEADERS, timeout=15)
        soup = BeautifulSoup(r.text, "html.parser")
        products = soup.select("a.product-name, a[href*='millesima.fr']")
        for el in products:
            href = el.get("href", "")
            text = el.get_text(strip=True).lower()
            if "blanc" in text or "white" in text:
                for vintage in TARGET_VINTAGES:
                    if str(vintage) in text or str(vintage) in href:
                        full_url = href if href.startswith("http") else f"https://www.millesima.fr{href}"
                        results.append({
                            "estate": estate_key, "retailer": "millesima",
                            "vintage": vintage, "url": full_url,
                            "price_selector": "span.price", "wine_color": "Blanc"
                        })
    except Exception as e:
        print(f"  millesima search failed: {e}")
    return results


def search_12bouteilles(estate_key, search_term):
    """Search 12bouteilles for white wine variants."""
    results = []
    url = f"https://www.12bouteilles.com/fr/recherche?q={search_term.replace(' ', '+')}"
    try:
        r = requests.get(url, headers=REQUESTS_HEADERS, timeout=15)
        soup = BeautifulSoup(r.text, "html.parser")
        products = soup.select("a[href*='12bouteilles.com']")
        for el in products:
            href = el.get("href", "")
            text = el.get_text(strip=True).lower()
            full_text = (href + " " + text).lower()
            if "blanc" in full_text or "white" in full_text:
                for vintage in TARGET_VINTAGES:
                    if str(vintage) in full_text:
                        full_url = href if href.startswith("http") else f"https://www.12bouteilles.com{href}"
                        results.append({
                            "estate": estate_key, "retailer": "12bouteilles",
                            "vintage": vintage, "url": full_url,
                            "price_selector": "span.prix_unit", "wine_color": "Blanc"
                        })
    except Exception as e:
        print(f"  12bouteilles search failed: {e}")
    return results


def search_vinatis(estate_key, search_term):
    """Search Vinatis for white wine variants."""
    results = []
    url = f"https://www.vinatis.com/recherche?q={search_term.replace(' ', '+')}"
    try:
        r = requests.get(url, headers=REQUESTS_HEADERS, timeout=15)
        soup = BeautifulSoup(r.text, "html.parser")
        products = soup.select("a[href*='vinatis.com']")
        for el in products:
            href = el.get("href", "")
            text = el.get_text(strip=True).lower()
            full_text = (href + " " + text).lower()
            if "blanc" in full_text or "white" in full_text:
                for vintage in TARGET_VINTAGES:
                    if str(vintage) in full_text:
                        full_url = href if href.startswith("http") else f"https://www.vinatis.com{href}"
                        results.append({
                            "estate": estate_key, "retailer": "vinatis",
                            "vintage": vintage, "url": full_url,
                            "price_selector": "span.price", "wine_color": "Blanc"
                        })
    except Exception as e:
        print(f"  vinatis search failed: {e}")
    return results


def search_cavissima(estate_key, search_term):
    """Search Cavissima for white wine variants."""
    results = []
    url = f"https://www.cavissima.com/search?q={search_term.replace(' ', '+')}"
    try:
        r = requests.get(url, headers=REQUESTS_HEADERS, timeout=15)
        soup = BeautifulSoup(r.text, "html.parser")
        links = soup.select("a[href*='/products/']")
        for el in links:
            href = el.get("href", "")
            text = el.get_text(strip=True).lower()
            full_text = (href + " " + text).lower()
            if "blanc" in full_text or "white" in full_text:
                for vintage in TARGET_VINTAGES:
                    if str(vintage) in full_text:
                        full_url = href if href.startswith("http") else f"https://www.cavissima.com{href}"
                        results.append({
                            "estate": estate_key, "retailer": "cavissima",
                            "vintage": vintage, "url": full_url,
                            "price_selector": "span.price-item__unit", "wine_color": "Blanc"
                        })
    except Exception as e:
        print(f"  cavissima search failed: {e}")
    return results


SEARCHERS = [
    search_millesima,
    search_12bouteilles,
    search_vinatis,
    search_cavissima,
]


def run_discovery():
    all_results = []

    for estate_key, search_terms in WHITE_ESTATES.items():
        print(f"\n{'='*60}")
        print(f"Searching: {estate_key}")
        print(f"{'='*60}")

        for search_fn in SEARCHERS:
            for term in search_terms[:1]:  # use first term per retailer
                results = search_fn(estate_key, term)
                if results:
                    # Deduplicate by (retailer, vintage, url)
                    seen = set()
                    unique = []
                    for r in results:
                        key = (r["retailer"], r["vintage"], r["url"])
                        if key not in seen:
                            seen.add(key)
                            unique.append(r)

                    print(f"  {search_fn.__name__}: {len(unique)} found")
                    for r in unique[:5]:
                        print(f"    {r['vintage']} -> {r['url'][:80]}")
                    all_results.extend(unique)
                else:
                    print(f"  {search_fn.__name__}: 0 found")
                polite_delay(1.0, 2.0)

    # Deduplicate all results
    seen = set()
    unique_all = []
    for r in all_results:
        key = (r["estate"], r["retailer"], r["vintage"])
        if key not in seen:
            seen.add(key)
            unique_all.append(r)

    print(f"\n\nTotal unique candidates: {len(unique_all)}")

    # Export to CSV
    import csv
    output_file = "white_wine_candidates.csv"
    with open(output_file, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=["estate", "retailer", "vintage", "url", "price_selector", "wine_color"])
        writer.writeheader()
        writer.writerows(unique_all)
    print(f"Exported to {output_file}")
    print("\nNext step: review white_wine_candidates.csv, verify URLs, then add to master_products.csv")

    return unique_all


if __name__ == "__main__":
    run_discovery()
