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
    "Chateau Lagarde",
    "Chateau La Louviere",
    "Chateau Bouscaut",
    "Chateau Lespault-Martillac",
]

# Listing pages: estate catalog pages that can be crawled for product links.
# key = (retailer, estate_name), value = catalog page URL
LISTING_PAGES: dict[tuple[str, str], str] = {

    # ── cercledemartillac.fr ─────────────────────────────────────────────────
    # Estate's own shop — LTM only
    ("cercledemartillac", "Chateau Latour Martillac"):
        "https://www.cercledemartillac.fr/10-chateau-latour-martillac",

    # ── vin-malin.fr (PrestaShop) ─────────────────────────────────────────────
    ("vin_malin", "Chateau Latour Martillac"):
        "https://www.vin-malin.fr/185-chateau-latour-martillac",
    ("vin_malin", "Chateau Larrivet Haut-Brion"):
        "https://www.vin-malin.fr/875-chateau-larrivet-haut-brion",
    ("vin_malin", "Chateau de Fieuzal"):
        "https://www.vin-malin.fr/182-chateau-de-fieuzal",
    ("vin_malin", "Chateau Sociando-Mallet"):
        "https://www.vin-malin.fr/169-chateau-sociando-mallet",

    # vinotheque-bordeaux.com estate-filtered listing pages returned HTTP 404
    # as of 2026-04-10 — their URL structure appears to have changed.
    # Individual product URLs in master_products.csv still work for scraping.

    # ── vintageandco.com (/liste.{slug}.html pattern) ─────────────────────────
    ("vintageandco", "Chateau Latour Martillac"):
        "https://www.vintageandco.com/liste.chateau-latour-martillac.html",
    ("vintageandco", "Chateau Carbonnieux"):
        "https://www.vintageandco.com/liste.chateau-carbonnieux.html",
    ("vintageandco", "Chateau Olivier"):
        "https://www.vintageandco.com/liste.chateau-olivier.html",
    ("vintageandco", "Chateau Larrivet Haut-Brion"):
        "https://www.vintageandco.com/liste.chateau-larrivet-haut-brion.html",
    ("vintageandco", "Chateau de Fieuzal"):
        "https://www.vintageandco.com/liste.chateau-de-fieuzal.html",
    ("vintageandco", "Chateau Sociando-Mallet"):
        "https://www.vintageandco.com/liste.chateau-sociando-mallet.html",
    ("vintageandco", "Chateau Malartic-Lagraviere"):
        "https://www.vintageandco.com/liste.chateau-malartic-lagraviere.html",

    # ── cavissima.com (Shopify /achat-vin/par-regions/bordeaux/{slug}/) ───────
    ("cavissima", "Chateau Latour Martillac"):
        "https://www.cavissima.com/achat-vin/par-regions/bordeaux/chateau-latour-martillac/",
    ("cavissima", "Chateau Carbonnieux"):
        "https://www.cavissima.com/achat-vin/par-regions/bordeaux/chateau-carbonnieux/",
    ("cavissima", "Chateau Olivier"):
        "https://www.cavissima.com/achat-vin/par-regions/bordeaux/chateau-olivier/",
    ("cavissima", "Chateau Larrivet Haut-Brion"):
        "https://www.cavissima.com/achat-vin/par-regions/bordeaux/chateau-larrivet-haut-brion/",
    ("cavissima", "Chateau de Fieuzal"):
        "https://www.cavissima.com/achat-vin/par-regions/bordeaux/chateau-de-fieuzal/",
    ("cavissima", "Chateau Sociando-Mallet"):
        "https://www.cavissima.com/achat-vin/par-regions/bordeaux/chateau-sociando-mallet/",
    ("cavissima", "Chateau Malartic-Lagraviere"):
        "https://www.cavissima.com/achat-vin/par-regions/bordeaux/chateau-malartic-lagraviere/",

    # ── lewineclub.com (/fr/{id}-{slug} pattern) ─────────────────────────────
    ("wineclub", "Chateau Latour Martillac"):
        "https://www.lewineclub.com/en/732-chateau-latour-martillac",
    ("wineclub", "Chateau Carbonnieux"):
        "https://www.lewineclub.com/fr/186-chateau-carbonnieux",
    ("wineclub", "Chateau Malartic-Lagraviere"):
        "https://www.lewineclub.com/fr/746-chateau-malartic-lagraviere",

    # ── 12bouteilles.com (/en/{id}-{slug} pattern) ───────────────────────────
    ("12bouteilles", "Chateau Latour Martillac"):
        "https://www.12bouteilles.com/en/240-chateau-latour-martillac",
    ("12bouteilles", "Chateau Carbonnieux"):
        "https://www.12bouteilles.com/en/234-chateau-carbonnieux",
    ("12bouteilles", "Chateau Sociando-Mallet"):
        "https://www.12bouteilles.com/en/221-chateau-sociando-mallet",
    ("12bouteilles", "Chateau Larrivet Haut-Brion"):
        "https://www.12bouteilles.com/en/232-chateau-larrivet-haut-brion",

    # ── aries-vins.com (/{id}-{slug} pattern) ────────────────────────────────
    ("aries", "Chateau Latour Martillac"):
        "https://aries-vins.com/114-chateau-la-tour-martillac",
    ("aries", "Chateau Carbonnieux"):
        "https://aries-vins.com/43-chateau-carbonnieux",

}
# NOTE: Chateau Lagarde, La Louviere, Bouscaut, Lespault-Martillac are not carried
# by cavissima or vintageandco via their standard estate listing-page patterns.
# They are tracked via URL probing (chateauinternet) and the gap report only.

# URL probers: (retailer, estate) -> function(vintage) -> candidate URL
# The prober returns None if it cannot construct a URL for this estate.
# Add new entries here as you discover URL patterns for other retailers.
CHATEAUINTERNET_SLUGS: dict[str, str] = {
    "Chateau Latour Martillac":    "chateau-latour-martillac-rouge",
    "Chateau Carbonnieux":         "chateau-carbonnieux-rouge",
    "Chateau Olivier":             "chateau-olivier-rouge",
    "Chateau Larrivet Haut-Brion": "chateau-larrivet-haut-brion-rouge",
    "Chateau de Fieuzal":          "chateau-de-fieuzal-rouge",
    "Chateau Sociando-Mallet":     "chateau-sociando-mallet",   # no -rouge
    "Chateau Malartic-Lagraviere": "chateau-malartic-lagraviere-rouge",
    "Chateau Lagarde":             "chateau-lagarde-rouge",
    "Chateau La Louviere":         "chateau-la-louviere-rouge",
    "Chateau Bouscaut":            "chateau-bouscaut-rouge",
    "Chateau Lespault-Martillac":  "chateau-lespault-martillac-rouge",
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


def _strip_tracking_params(url: str) -> str:
    """Return URL with known retailer tracking query params removed."""
    if "?" not in url:
        return url
    base, qs = url.split("?", 1)
    # Cavissima: ?_pos=...&_fid=...&_ss=...&variant=...
    # Chateaunet: ?_gl=...
    # Keep the base URL only — tracking params are not part of the canonical URL
    return base


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
            raw_url = row.get("product_url", "").strip()
            products.append(KnownProduct(
                retailer=row["retailer"],
                estate_name=row["estate_name"],
                vintage=vintage,
                url=_strip_tracking_params(raw_url),
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


_ESTATE_SLUG: dict[str, str] = {
    "Chateau Latour Martillac":    "latour-martillac",
    "Chateau Carbonnieux":         "carbonnieux",
    "Chateau Olivier":             "chateau-olivier",
    "Chateau Larrivet Haut-Brion": "larrivet-haut-brion",
    "Chateau de Fieuzal":          "de-fieuzal",
    "Chateau Sociando-Mallet":     "sociando-mallet",
    "Chateau Malartic-Lagraviere": "malartic-lagraviere",
    "Chateau Lagarde":             "lagarde",
    "Chateau La Louviere":         "la-louviere",
    "Chateau Bouscaut":            "bouscaut",
    "Chateau Lespault-Martillac":  "lespault-martillac",
}

# URL path segments that are never individual product pages
_NON_PRODUCT_SEGMENTS = {
    "primeurs", "collections", "coffrets-cadeaux", "gros-formats",
    "vins-blancs", "promotions", "blog", "actualites",
}


def _is_product_url(href: str, estate: str) -> bool:
    """Return True only if href looks like a rouge single-product 75cl page for this estate."""
    # Must contain a tracked vintage year (2015 to CURRENT_YEAR inclusive)
    if not any(str(y) in href for y in range(2015, CURRENT_YEAR + 1)):
        return False
    # Skip blanc / white wines (French and English labels in URL)
    if re.search(r"\b(blanc|white)\b", href, re.IGNORECASE):
        return False
    # Skip large-format bottles
    if re.search(r"\b(magnum|imperiale|jeroboam|double.magnum|balthazar)\b", href, re.IGNORECASE):
        return False
    # Skip second-label / sub-brand wines (e.g. "la-demoiselle-de", "l-abeille-de")
    if re.search(r"\bl.abeille\b|\bdemoiselle\b|\bdemoiselles\b", href, re.IGNORECASE):
        return False
    # Skip ?q= filtered category views (not individual product pages)
    if "?q=" in href:
        return False
    # Skip category / collection pages by segment
    path = href.split("?")[0].rstrip("/")
    if any(seg in _NON_PRODUCT_SEGMENTS for seg in path.split("/")):
        return False
    # Must mention this estate's slug somewhere in the URL
    slug = _ESTATE_SLUG.get(estate, "")
    if slug and slug not in href.lower():
        return False
    return True


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

        domain = catalog_url.split("/")[2]
        seen: set[str] = set()
        new: list[str] = []

        for a in soup.find_all("a", href=True):
            href: str = a["href"].strip()
            # Normalise to absolute URL
            if href.startswith("/"):
                href = f"https://{domain}{href}"
            if not href.startswith("http"):
                continue
            # Strip URL fragments — different pack-size variants of the same product
            href = href.split("#")[0].rstrip("/")
            if href in known_urls or href in seen:
                continue
            seen.add(href)
            if _is_product_url(href, estate):
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
        print("  -> Add confirmed URLs to master_products.csv and push to trigger scrape.")

    # Write JSON report
    if not args.no_json:
        report = build_json_report(gaps, listings, probes)
        REPORT_PATH.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"  -> Report written to {REPORT_PATH}")


if __name__ == "__main__":
    main()
