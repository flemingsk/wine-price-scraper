"""
src/url_discovery.py — Daily new-URL discovery for tracked estates.

Run before the main scraper to surface new product pages not yet in
master_products.csv. Three complementary strategies:

  A. Coverage-gap report  — shows (retailer × estate × vintage) holes
                            across all retailers so you can act on them.
  B. Listing-page crawl   — fetches estate catalog pages on retailers that
                            expose them (cercledemartillac, vin-malin,
                            vinotheque-bordeaux) and extracts links not in CSV.
  C. URL-pattern probe    — constructs candidate URLs for missing vintages on
                            retailers with predictable slug patterns
                            (chateauinternet) and validates with HTTP GET.

Usage:
    python -m src.url_discovery              # full report to stdout + JSON
    python -m src.url_discovery --gaps-only  # gap table only (fast)
    python -m src.url_discovery --probe      # also run slow URL probing
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import re
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import requests
from bs4 import BeautifulSoup

from src.scrapers.browser_utils import REQUESTS_HEADERS

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

CSV_PATH = Path("master_products.csv")
REPORT_PATH = Path("url_discovery_report.json")

CURRENT_YEAR = date.today().year
PROBE_VINTAGES = list(range(2015, CURRENT_YEAR + 1))  # years to probe for

ESTATES = [
    "Chateau Latour Martillac",
    "Chateau Carbonnieux",
    "Chateau Olivier",
    "Chateau Larrivet Haut-Brion",
    "Chateau de Fieuzal",
    "Chateau Sociando-Mallet",
    "Chateau Malartic-Lagraviere",
]

# Listing pages: estate catalog pages that can be crawled for product links.
# key = (retailer, estate_name), value = catalog page URL
LISTING_PAGES: dict[tuple[str, str], str] = {
    # cercledemartillac.fr — estate's own shop, LTM only
    ("cercledemartillac", "Chateau Latour Martillac"):
        "https://www.cercledemartillac.fr/10-chateau-latour-martillac",

    # vin-malin.fr — known catalog page IDs
    ("vin_malin", "Chateau Latour Martillac"):
        "https://www.vin-malin.fr/185-chateau-latour-martillac",
    ("vin_malin", "Chateau Larrivet Haut-Brion"):
        "https://www.vin-malin.fr/875-chateau-larrivet-haut-brion",
    ("vin_malin", "Chateau de Fieuzal"):
        "https://www.vin-malin.fr/182-chateau-de-fieuzal",
    ("vin_malin", "Chateau Sociando-Mallet"):
        "https://www.vin-malin.fr/169-chateau-sociando-mallet",

    # vinotheque-bordeaux.com — estate-filtered listing pages
    ("vinotheque_bordeaux", "Chateau Latour Martillac"):
        "https://www.vinotheque-bordeaux.com/fr/bordeaux/rouge/pessac-leognan/fiche-6652-25466-chateau-latour-martillac.html",
    ("vinotheque_bordeaux", "Chateau Olivier"):
        "https://www.vinotheque-bordeaux.com/fr/bordeaux/rouge/pessac-leognan/fiche-6590-35833-chateau-olivier.html",
    ("vinotheque_bordeaux", "Chateau Larrivet Haut-Brion"):
        "https://www.vinotheque-bordeaux.com/fr/bordeaux/rouge/pessac-leognan/fiche-6648-39555-chateau-larrivet-haut-brion.html",
}

# URL probers: (retailer, estate) → function(vintage) → candidate URL
# The prober returns None if it cannot construct a URL for this estate.
# Add new entries here as you discover URL patterns for other retailers.
CHATEAUINTERNET_SLUGS: dict[str, str] = {
    "Chateau Latour Martillac":   "chateau-latour-martillac-rouge",
    "Chateau Carbonnieux":        "chateau-carbonnieux-rouge",
    "Chateau Olivier":            "chateau-olivier-rouge",
    "Chateau Larrivet Haut-Brion":"chateau-larrivet-haut-brion-rouge",
    "Chateau de Fieuzal":         "chateau-de-fieuzal-rouge",
    "Chateau Sociando-Mallet":    "chateau-sociando-mallet",   # no -rouge
    "Chateau Malartic-Lagraviere":"chateau-malartic-lagraviere-rouge",
}


def _chateauinternet_url(estate: str, vintage: int) -> str | None:
    slug = CHATEAUINTERNET_SLUGS.get(estate)
    if not slug:
        return None
    return f"https://www.chateauinternet.com/{slug}-{vintage}"


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------

@dataclass
class KnownProduct:
    retailer: str
    estate_name: str
    vintage: int
    url: str


def load_known_products() -> list[KnownProduct]:
    """Read master_products.csv and return all active rouge entries."""
    products: list[KnownProduct] = []
    with open(CSV_PATH, encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            if row.get("active", "").upper() != "TRUE":
                continue
            notes = row.get("notes", "").lower()
            if "blanc" in notes:
                continue
            try:
                vintage = int(row["vintage_start"])
            except (ValueError, KeyError):
                continue
            products.append(KnownProduct(
                retailer=row["retailer"],
                estate_name=row["estate_name"],
                vintage=vintage,
                url=row["product_url"],
            ))
    return products


# ---------------------------------------------------------------------------
# Strategy A: Coverage gap report
# ---------------------------------------------------------------------------

@dataclass
class GapReport:
    estate: str
    retailer: str
    known_vintages: list[int]
    missing_vintages: list[int]   # in PROBE_VINTAGES range not covered


def coverage_gap_report(products: list[KnownProduct]) -> list[GapReport]:
    """For every (retailer × estate) combo, show which vintages are missing."""
    by_key: dict[tuple[str, str], list[int]] = defaultdict(list)
    for p in products:
        by_key[(p.retailer, p.estate_name)].append(p.vintage)

    gaps: list[GapReport] = []
    for (retailer, estate), vintages in sorted(by_key.items()):
        known = sorted(vintages)
        missing = [y for y in PROBE_VINTAGES if y not in known]
        gaps.append(GapReport(
            estate=estate,
            retailer=retailer,
            known_vintages=known,
            missing_vintages=missing,
        ))
    return gaps


# ---------------------------------------------------------------------------
# Strategy B: Listing-page crawl
# ---------------------------------------------------------------------------

@dataclass
class ListingDiscovery:
    retailer: str
    estate: str
    catalog_url: str
    new_urls: list[str] = field(default_factory=list)
    error: str | None = None


def crawl_listing_page(
    retailer: str,
    estate: str,
    catalog_url: str,
    known_urls: set[str],
) -> ListingDiscovery:
    """Fetch a catalog page and return product links not already in the CSV."""
    result = ListingDiscovery(retailer=retailer, estate=estate, catalog_url=catalog_url)
    try:
        resp = requests.get(catalog_url, headers=REQUESTS_HEADERS, timeout=20)
        if resp.status_code != 200:
            result.error = f"HTTP {resp.status_code}"
            return result
        soup = BeautifulSoup(resp.text, "html.parser")

        # Extract all product <a> links from the page
        domain = catalog_url.split("/")[2]
        new: list[str] = []
        for a in soup.find_all("a", href=True):
            href: str = a["href"].strip()
            # Normalise to absolute URL
            if href.startswith("/"):
                href = f"https://{domain}{href}"
            if not href.startswith("http"):
                continue
            # Skip clearly non-product links (navigation, categories, etc.)
            if not re.search(r"\d{4}", href):   # must contain a year
                continue
            if href in known_urls:
                continue
            # Only count links that contain a recognised vintage year
            if not any(str(y) in href for y in range(2010, CURRENT_YEAR + 2)):
                continue
            if href not in new:
                new.append(href)

        result.new_urls = sorted(new)
        time.sleep(1.5)
    except Exception as exc:
        result.error = str(exc)
    return result


def run_listing_crawl(products: list[KnownProduct]) -> list[ListingDiscovery]:
    known_urls = {p.url for p in products}
    results: list[ListingDiscovery] = []
    for (retailer, estate), catalog_url in LISTING_PAGES.items():
        logger.info(f"  crawling {retailer} / {estate}")
        result = crawl_listing_page(retailer, estate, catalog_url, known_urls)
        results.append(result)
    return results


# ---------------------------------------------------------------------------
# Strategy C: URL-pattern probing
# ---------------------------------------------------------------------------

@dataclass
class ProbeDiscovery:
    retailer: str
    estate: str
    new_urls: list[str] = field(default_factory=list)


def probe_url(url: str) -> bool:
    """Return True if the URL responds with 200."""
    try:
        r = requests.get(url, headers=REQUESTS_HEADERS, timeout=15, allow_redirects=True)
        return r.status_code == 200
    except Exception:
        return False


def run_url_probing(products: list[KnownProduct]) -> list[ProbeDiscovery]:
    """
    For chateauinternet.com, probe every (estate × missing vintage) combo
    to see if a page exists that isn't yet in the CSV.
    """
    known_set: set[tuple[str, str, int]] = {
        (p.retailer, p.estate_name, p.vintage) for p in products
    }

    results: list[ProbeDiscovery] = []
    for estate in ESTATES:
        discovery = ProbeDiscovery(retailer="chateauinternet", estate=estate)
        for vintage in PROBE_VINTAGES:
            if ("chateauinternet", estate, vintage) in known_set:
                continue
            url = _chateauinternet_url(estate, vintage)
            if not url:
                continue
            logger.info(f"  probing chateauinternet {estate} {vintage}")
            if probe_url(url):
                discovery.new_urls.append(url)
            time.sleep(0.8)
        if discovery.new_urls:
            results.append(discovery)
    return results


# ---------------------------------------------------------------------------
# Report formatting & output
# ---------------------------------------------------------------------------

def print_gap_table(gaps: list[GapReport]) -> None:
    print("\n" + "=" * 70)
    print("COVERAGE GAP REPORT")
    print("Retailers × estates where vintages are missing from master CSV")
    print("=" * 70)

    # Only show gaps where something recent (last 2 years) is missing
    recent_missing = [
        g for g in gaps
        if any(y >= CURRENT_YEAR - 2 for y in g.missing_vintages)
    ]
    if not recent_missing:
        print("  No recent-vintage gaps found.")
        return

    prev_estate = None
    for g in sorted(recent_missing, key=lambda x: (x.estate, x.retailer)):
        if g.estate != prev_estate:
            print(f"\n  {g.estate}")
            prev_estate = g.estate
        recent = [y for y in g.missing_vintages if y >= CURRENT_YEAR - 2]
        print(f"    [{g.retailer}]  missing recent: {recent}  |  known: {g.known_vintages}")


def print_listing_results(results: list[ListingDiscovery]) -> None:
    print("\n" + "=" * 70)
    print("LISTING-PAGE CRAWL RESULTS")
    print("New product URLs found on catalog pages (not in master CSV)")
    print("=" * 70)
    found_any = False
    for r in results:
        if r.error:
            print(f"  [{r.retailer}] {r.estate}: ERROR — {r.error}")
            continue
        if r.new_urls:
            found_any = True
            print(f"\n  [{r.retailer}] {r.estate} — {len(r.new_urls)} new URL(s):")
            for url in r.new_urls:
                print(f"    {url}")
    if not found_any:
        print("  No new URLs found on listing pages.")


def print_probe_results(results: list[ProbeDiscovery]) -> None:
    print("\n" + "=" * 70)
    print("URL-PATTERN PROBE RESULTS (chateauinternet.com)")
    print("New pages that respond 200 for missing vintages")
    print("=" * 70)
    if not results:
        print("  No new URLs found via URL probing.")
        return
    for r in results:
        print(f"\n  {r.estate} — {len(r.new_urls)} new URL(s):")
        for url in r.new_urls:
            print(f"    {url}")


def build_json_report(
    gaps: list[GapReport],
    listings: list[ListingDiscovery],
    probes: list[ProbeDiscovery],
) -> dict:
    return {
        "generated_at": date.today().isoformat(),
        "current_year": CURRENT_YEAR,
        "coverage_gaps": [
            {
                "retailer": g.retailer,
                "estate": g.estate,
                "known_vintages": g.known_vintages,
                "missing_in_probe_range": g.missing_vintages,
            }
            for g in gaps
            if g.missing_vintages
        ],
        "listing_page_discoveries": [
            {
                "retailer": r.retailer,
                "estate": r.estate,
                "catalog_url": r.catalog_url,
                "new_urls": r.new_urls,
                "error": r.error,
            }
            for r in listings
        ],
        "url_probe_discoveries": [
            {
                "retailer": r.retailer,
                "estate": r.estate,
                "new_urls": r.new_urls,
            }
            for r in probes
        ],
        "action_items": _build_action_items(listings, probes),
    }


def _build_action_items(
    listings: list[ListingDiscovery],
    probes: list[ProbeDiscovery],
) -> list[dict]:
    """Flatten all newly discovered URLs into a ready-to-act list."""
    items: list[dict] = []
    for r in listings:
        for url in r.new_urls:
            items.append({
                "action": "add_to_csv",
                "retailer": r.retailer,
                "estate": r.estate,
                "url": url,
                "price_selector": "span.price",
                "source": "listing_page",
            })
    for r in probes:
        for url in r.new_urls:
            # Extract vintage from URL
            m = re.search(r"-(\d{4})$", url)
            vintage = int(m.group(1)) if m else None
            items.append({
                "action": "add_to_csv",
                "retailer": r.retailer,
                "estate": r.estate,
                "url": url,
                "vintage": vintage,
                "price_selector": "div.price",
                "source": "url_probe",
            })
    return items


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Discover new wine product URLs")
    parser.add_argument("--gaps-only", action="store_true",
                        help="Print coverage gap table only (no HTTP requests)")
    parser.add_argument("--probe", action="store_true",
                        help="Also run URL-pattern probing (slow, ~100 HTTP requests)")
    parser.add_argument("--no-json", action="store_true",
                        help="Skip writing url_discovery_report.json")
    args = parser.parse_args()

    print(f"\nURL Discovery — {date.today()}  (tracking up to {CURRENT_YEAR})")

    logger.info("Loading master_products.csv…")
    products = load_known_products()
    logger.info(f"  {len(products)} active rouge entries loaded")

    # Strategy A: gap report (always runs, free)
    gaps = coverage_gap_report(products)
    print_gap_table(gaps)

    listings: list[ListingDiscovery] = []
    probes: list[ProbeDiscovery] = []

    if not args.gaps_only:
        # Strategy B: listing-page crawl
        logger.info("\nRunning listing-page crawl…")
        listings = run_listing_crawl(products)
        print_listing_results(listings)

        # Strategy C: URL probing (opt-in, slow)
        if args.probe:
            logger.info("\nRunning URL-pattern probing (chateauinternet)…")
            probes = run_url_probing(products)
            print_probe_results(probes)

    # Summary of actionable items
    all_new = [u for r in listings for u in r.new_urls] + \
              [u for r in probes for u in r.new_urls]
    print(f"\n{'=' * 70}")
    print(f"SUMMARY: {len(all_new)} new URL(s) to review across all strategies.")
    if all_new:
        print("  → Add confirmed URLs to master_products.csv and push to trigger scrape.")

    # Write JSON report
    if not args.no_json:
        report = build_json_report(gaps, listings, probes)
        REPORT_PATH.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"  → Report written to {REPORT_PATH}")


if __name__ == "__main__":
    main()
