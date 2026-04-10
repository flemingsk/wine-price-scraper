"""
src/url_discovery.py — Daily new-URL discovery for tracked estates.

Run before the main scraper to surface new product pages not yet in
master_products.csv.  Three complementary strategies:

  A. Coverage-gap report  — shows (retailer x estate x vintage) holes.
  B. Listing-page crawl   — fetches estate catalog pages on retailers that
                            expose predictable listing pages and extracts
                            links not in CSV.
  C. URL-pattern probe    — constructs candidate URLs for missing
                            (estate x vintage) pairs on retailers with
                            predictable slug patterns and validates with
                            HTTP GET.

All estates are derived from master_products.csv at runtime — no hardcoded
list to maintain.  Adding a new estate OR a new retailer to the CSV
automatically expands the discovery scope on the next run:

  * If a new estate is added to the CSV, strategies B and C will probe it
    across every retailer that has a known URL pattern on the next run.
  * If a new retailer is registered in URL_BUILDERS or LISTING_URL_BUILDERS,
    it will be probed against every estate already in the CSV.

Usage:
    python -m src.url_discovery              # strategies A + B (listing crawl)
    python -m src.url_discovery --probe      # + strategy C (URL probing, slow)
    python -m src.url_discovery --auto-append [--probe]  # write new rows to CSV
    python -m src.url_discovery --gaps-only  # gap table only (no HTTP requests)
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
from typing import Callable

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


# ---------------------------------------------------------------------------
# Slug helpers
# ---------------------------------------------------------------------------

def _default_slug(estate: str) -> str:
    """Lowercase, spaces to hyphens: 'Chateau La Louviere' -> 'chateau-la-louviere'."""
    return estate.lower().replace(" ", "-")


def _estate_filter_slug(estate: str) -> str:
    """
    Short identifying substring used to verify a discovered URL is for this
    estate (not another wine on the same listing page).

    Strips the 'Chateau' prefix and returns the identifying portion, e.g.:
      'Chateau Latour Martillac'  -> 'latour-martillac'
      'Chateau de Fieuzal'        -> 'de-fieuzal'
      'Chateau Malartic-Lagraviere' -> 'malartic-lagraviere'
    """
    s = re.sub(r"(?i)^chateau\s+(de|la|le|les|du|l')\s+", r"\1 ", estate)
    s = re.sub(r"(?i)^chateau\s+", "", s)
    return s.lower().replace(" ", "-")


# ---------------------------------------------------------------------------
# Per-retailer slug maps (only needed where slug != _default_slug(estate))
# ---------------------------------------------------------------------------

# chateauinternet: most estates append '-rouge'; one exception
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

# millesimes.com: slug derived from estate name, suffix varies by appellation.
# URL pattern: millesimes.com/{prefix}_{vintage}{suffix}
# Observed from existing CSV rows — add new estates when URLs are confirmed.
MILLESIMES_SLUGS: dict[str, tuple[str, str]] = {
    # estate_name: (url_prefix_before_vintage, url_suffix_after_vintage)
    "Chateau Carbonnieux":         ("Carbonnieux",          "_cru_classe_Pessac-Leognan_vin_rouge"),
    "Chateau Latour Martillac":    ("Latour_martillac",     "_cru_classe_de_graves_Pessac-Leognan_vin_rouge"),
    "Chateau Malartic-Lagraviere": ("Malartic_lagraviere_rouge", "_grand_cru_classe_Graves_vin_rouge"),
    "Chateau Sociando-Mallet":     ("Sociando_mallet",      "_Haut-Medoc_vin_rouge"),
}


# ---------------------------------------------------------------------------
# Strategy C config: URL-pattern probers
#
# Each entry: retailer -> (slug_fn, url_fn)
#   slug_fn(estate) -> slug string (or None to skip this estate)
#   url_fn(slug, vintage) -> candidate URL
#
# Adding a retailer here causes it to be probed against EVERY estate in the
# CSV that slug_fn can map.
# ---------------------------------------------------------------------------

URL_BUILDERS: dict[str, tuple[
    Callable[[str], str | None],   # slug_fn: returns None to skip this estate
    Callable[[str, int], str],     # url_fn: builds the candidate URL
]] = {
    "chateauinternet": (
        lambda estate: CHATEAUINTERNET_SLUGS.get(estate),
        lambda slug, v: f"https://www.chateauinternet.com/{slug}-{v}",
    ),
    "cashvin": (
        # /produit/{slug}-{vintage}/ — slug = lowercase-hyphenated estate name
        _default_slug,
        lambda slug, v: f"https://www.cashvin.com/produit/{slug}-{v}/",
    ),
    "cavissima": (
        # /products/{slug}-{vintage} — confirmed from existing CSV rows
        _default_slug,
        lambda slug, v: f"https://www.cavissima.com/products/{slug}-{v}",
    ),
    "levindevantsoi": (
        # /boutique/{slug}-pessac-leognan-{vintage}/ — confirmed from CSV
        # Estates outside Pessac-Léognan (e.g. Sociando-Mallet) will 404 — handled gracefully
        _default_slug,
        lambda slug, v: f"https://www.levindevantsoi.com/boutique/{slug}-pessac-leognan-{v}/",
    ),
    "millesimes": (
        # URL pattern varies per estate; only known estates are probed
        lambda estate: MILLESIMES_SLUGS.get(estate),   # returns None for unknown estates
        lambda slug_tuple, v: (
            f"https://millesimes.com/{slug_tuple[0]}_{v}{slug_tuple[1]}"
        ),
    ),
}


# ---------------------------------------------------------------------------
# Strategy B config: Listing-page crawlers
#
# Two sub-types:
#   LISTING_URL_BUILDERS — retailers whose listing page URL can be derived
#     from the estate name automatically.  Adding a new estate to the CSV
#     automatically adds it to the crawl for these retailers.
#
#   MANUAL_LISTING_PAGES — retailers whose listing page IDs/URLs must be
#     specified manually (ID-based paths that can't be predicted).
# ---------------------------------------------------------------------------

# Retailers where listing page URL = f(estate_name) — auto-generated for all
# estates in the CSV.  Adding a retailer here triggers a crawl for every
# estate; adding a new estate to the CSV automatically adds it to every builder.
LISTING_URL_BUILDERS: dict[str, Callable[[str], str]] = {
    "cavissima": (
        lambda estate:
            f"https://www.cavissima.com/achat-vin/par-regions/bordeaux/"
            f"{_default_slug(estate)}/"
    ),
    "vintageandco": (
        lambda estate:
            f"https://www.vintageandco.com/liste.{_default_slug(estate)}.html"
    ),
    # Product URLs are /fr/{slug}/{id}-product.html — category = /fr/{slug}/
    "wineclub": (
        lambda estate: f"https://www.lewineclub.com/fr/{_default_slug(estate)}/"
    ),
    # Product URLs are /fr/{slug}/{id}-product.html — category = /fr/{slug}/
    "12bouteilles": (
        lambda estate: f"https://www.12bouteilles.com/fr/{_default_slug(estate)}/"
    ),
    # Product URLs are /{slug}-{vintage}/{id} — estate listing = /{slug}/
    "wineandco": (
        lambda estate: f"https://www.wineandco.com/{_default_slug(estate)}/"
    ),
}

# Region listing pages — a single URL covering all tracked estates for a
# retailer (e.g. a Pessac-Léognan appellation page).  Fetched ONCE per run;
# _is_product_url() filters links to each estate individually.
# Format: retailer -> URL
REGION_LISTING_PAGES: dict[str, str] = {
    # All tracked estates are Pessac-Léognan or Haut-Médoc — the full catalogue
    # page lists every wine; estate slug filtering picks out the right rows.
    "labouteilledoree": "https://www.labouteilledoree.com/en/pessac-leognan/",
}

# Retailers whose listing page URLs must be maintained manually (ID-based paths
# that cannot be predicted from the estate name alone).
# Add new (retailer, estate) entries here when a listing page is discovered.
MANUAL_LISTING_PAGES: dict[tuple[str, str], str] = {

    # ── cercledemartillac.fr ─────────────────────────────────────────────────
    ("cercledemartillac", "Chateau Latour Martillac"):
        "https://www.cercledemartillac.fr/10-chateau-latour-martillac",

    # ── vin-malin.fr ─────────────────────────────────────────────────────────
    ("vin_malin", "Chateau Latour Martillac"):
        "https://www.vin-malin.fr/185-chateau-latour-martillac",
    ("vin_malin", "Chateau Larrivet Haut-Brion"):
        "https://www.vin-malin.fr/875-chateau-larrivet-haut-brion",
    ("vin_malin", "Chateau de Fieuzal"):
        "https://www.vin-malin.fr/182-chateau-de-fieuzal",
    ("vin_malin", "Chateau Sociando-Mallet"):
        "https://www.vin-malin.fr/169-chateau-sociando-mallet",

    # ── aries-vins.com (ID-based category slugs) ─────────────────────────────
    ("aries", "Chateau Latour Martillac"):
        "https://aries-vins.com/114-chateau-la-tour-martillac",
    ("aries", "Chateau Carbonnieux"):
        "https://aries-vins.com/43-chateau-carbonnieux",
    ("aries", "Chateau La Louviere"):
        "https://aries-vins.com/104-chateau-la-louviere",
    # Add other aries estate listing pages here as they are found.

    # ── vinotheque-bordeaux.com (search endpoint) ─────────────────────────────
    # Estate listing pages 404; search results work and are filtered by estate
    # slug via _is_product_url().  One entry per estate using a short search term.
    ("vinotheque_bordeaux", "Chateau Latour Martillac"):
        "https://vinotheque-bordeaux.com/jolisearch?search_query=latour+martillac",
    ("vinotheque_bordeaux", "Chateau Carbonnieux"):
        "https://vinotheque-bordeaux.com/jolisearch?search_query=carbonnieux",
    ("vinotheque_bordeaux", "Chateau Olivier"):
        "https://vinotheque-bordeaux.com/jolisearch?search_query=chateau+olivier",
    ("vinotheque_bordeaux", "Chateau Larrivet Haut-Brion"):
        "https://vinotheque-bordeaux.com/jolisearch?search_query=larrivet",
    ("vinotheque_bordeaux", "Chateau de Fieuzal"):
        "https://vinotheque-bordeaux.com/jolisearch?search_query=fieuzal",
    ("vinotheque_bordeaux", "Chateau Sociando-Mallet"):
        "https://vinotheque-bordeaux.com/jolisearch?search_query=sociando",
    ("vinotheque_bordeaux", "Chateau Malartic-Lagraviere"):
        "https://vinotheque-bordeaux.com/jolisearch?search_query=malartic",
    ("vinotheque_bordeaux", "Chateau Lagarde"):
        "https://vinotheque-bordeaux.com/jolisearch?search_query=lagarde",
    ("vinotheque_bordeaux", "Chateau La Louviere"):
        "https://vinotheque-bordeaux.com/jolisearch?search_query=la+louviere",
    ("vinotheque_bordeaux", "Chateau Bouscaut"):
        "https://vinotheque-bordeaux.com/jolisearch?search_query=bouscaut",
    ("vinotheque_bordeaux", "Chateau Lespault-Martillac"):
        "https://vinotheque-bordeaux.com/jolisearch?search_query=lespault",

}


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------

@dataclass
class KnownProduct:
    retailer: str
    estate_name: str
    vintage: int
    url: str
    wine_color: str = "Rouge"


def _strip_tracking_params(url: str) -> str:
    """Return URL with known retailer tracking query params removed."""
    if "?" not in url:
        return url
    base, _ = url.split("?", 1)
    return base


def load_known_products() -> list[KnownProduct]:
    """Read master_products.csv and return all active entries (rouge + blanc)."""
    products: list[KnownProduct] = []
    with open(CSV_PATH, encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            if row.get("active", "").upper() != "TRUE":
                continue
            try:
                vintage = int(row["vintage_start"])
            except (ValueError, KeyError):
                continue
            raw_url = row.get("product_url", "").strip()
            wine_color = (row.get("wine_color") or "").strip() or "Rouge"
            products.append(KnownProduct(
                retailer=row["retailer"],
                estate_name=row["estate_name"],
                vintage=vintage,
                url=_strip_tracking_params(raw_url),
                wine_color=wine_color,
            ))
    return products


def _all_estates(products: list[KnownProduct]) -> list[str]:
    """Return sorted list of unique estate names from the CSV."""
    return sorted({p.estate_name for p in products})


# ---------------------------------------------------------------------------
# Strategy A: Coverage gap report
# ---------------------------------------------------------------------------

@dataclass
class GapReport:
    estate: str
    retailer: str
    wine_color: str
    known_vintages: list[int]
    missing_vintages: list[int]


def coverage_gap_report(products: list[KnownProduct]) -> list[GapReport]:
    by_key: dict[tuple[str, str, str], list[int]] = defaultdict(list)
    for p in products:
        by_key[(p.retailer, p.estate_name, p.wine_color)].append(p.vintage)

    gaps: list[GapReport] = []
    for (retailer, estate, wine_color), vintages in sorted(by_key.items()):
        known = sorted(vintages)
        missing = [y for y in PROBE_VINTAGES if y not in known]
        gaps.append(GapReport(
            estate=estate,
            retailer=retailer,
            wine_color=wine_color,
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
    wine_color: str = "Rouge"
    new_urls: list[str] = field(default_factory=list)
    error: str | None = None


# URL path segments that are never individual product pages
_NON_PRODUCT_SEGMENTS = {
    "primeurs", "collections", "coffrets-cadeaux", "gros-formats",
    "vins-blancs", "promotions", "blog", "actualites",
}


_LARGE_FORMAT_RE = re.compile(
    r"\b(magnum|imperiale|jeroboam|double[\s-]?magnum|balthazar|"
    r"mathusalem|nabuchodonosor|rehoboam|melchior|salmanazar|"
    r"1[,.]5\s*[Ll])\b",
    re.IGNORECASE,
)


def _is_product_url(
    href: str,
    estate: str,
    link_text: str = "",
    wine_color: str = "Rouge",
) -> bool:
    """Return True only if href looks like a single-product 75cl page for wine_color.

    link_text should be the visible anchor text (if available).  Many
    retailers use opaque numeric product IDs so the URL alone cannot
    distinguish a 75cl from a magnum, or rouge from blanc; the anchor
    text usually can.

    For blanc: the URL or anchor text must explicitly contain "blanc" or
    "white" — ambiguous links are skipped to avoid misclassification.
    """
    # Must contain a tracked vintage year (2015 to last completed year)
    if not any(str(y) in href for y in range(2015, CURRENT_YEAR)):
        return False
    # Skip large-format bottles — check both URL and anchor text
    if _LARGE_FORMAT_RE.search(href) or _LARGE_FORMAT_RE.search(link_text):
        return False
    # Skip second-label / sub-brand wines
    if re.search(r"\bl.abeille\b|\bdemoiselle\b|\bdemoiselles\b", href, re.IGNORECASE):
        return False
    # Skip search/filter views
    if "?q=" in href:
        return False
    # Skip category / collection pages
    path = href.split("?")[0].rstrip("/")
    if any(
        any(seg.startswith(kw) for kw in _NON_PRODUCT_SEGMENTS)
        for seg in path.split("/")
    ):
        return False
    # Color filtering — check URL and anchor text together
    combined = href.lower() + " " + link_text.lower()
    has_blanc = bool(re.search(r"\b(blanc|white)\b", combined))
    if wine_color.lower() == "rouge":
        if has_blanc:
            return False  # blanc product, not rouge
    else:  # Blanc
        if not has_blanc:
            return False  # can't confirm blanc — skip to avoid misclassification
    # Must mention this estate's identifying slug somewhere in the URL
    slug = _estate_filter_slug(estate)
    if slug and slug not in href.lower():
        return False
    return True


def crawl_listing_page(
    retailer: str,
    estate: str,
    catalog_url: str,
    known_urls: set[str],
) -> list[ListingDiscovery]:
    """Fetch a catalog page once and return per-color discoveries not in the CSV."""
    discs: dict[str, ListingDiscovery] = {
        color: ListingDiscovery(
            retailer=retailer, estate=estate, catalog_url=catalog_url, wine_color=color
        )
        for color in ("Rouge", "Blanc")
    }
    try:
        resp = requests.get(catalog_url, headers=REQUESTS_HEADERS, timeout=20)
        if resp.status_code != 200:
            for d in discs.values():
                d.error = f"HTTP {resp.status_code}"
            return list(discs.values())
        soup = BeautifulSoup(resp.text, "html.parser")

        domain = catalog_url.split("/")[2]
        seen: set[str] = set()

        for a in soup.find_all("a", href=True):
            href: str = a["href"].strip()
            if href.startswith("/"):
                href = f"https://{domain}{href}"
            if not href.startswith("http"):
                continue
            href = href.split("#")[0].rstrip("/")
            if href in known_urls or href in seen:
                continue
            seen.add(href)
            link_text = a.get_text(" ", strip=True)
            for color, disc in discs.items():
                if _is_product_url(href, estate, link_text=link_text, wine_color=color):
                    disc.new_urls.append(href)

        for d in discs.values():
            d.new_urls = sorted(d.new_urls)
        time.sleep(1.5)
    except Exception as exc:
        for d in discs.values():
            d.error = str(exc)
    return [d for d in discs.values() if d.new_urls or d.error]


def _build_all_listing_pages(
    products: list[KnownProduct],
) -> dict[tuple[str, str], str]:
    """
    Combine auto-generated and manual listing page URLs.

    For retailers in LISTING_URL_BUILDERS, a listing page is generated for
    every estate currently in the CSV.  MANUAL_LISTING_PAGES entries are
    merged in on top (and can override generated ones if needed).
    """
    all_estates = _all_estates(products)
    pages: dict[tuple[str, str], str] = {}

    # Auto-generated from estate name
    for retailer, url_fn in LISTING_URL_BUILDERS.items():
        for estate in all_estates:
            pages[(retailer, estate)] = url_fn(estate)

    # Manual / ID-based (merges last, so manual overrides auto if both exist)
    pages.update(MANUAL_LISTING_PAGES)
    return pages


def crawl_region_page(
    retailer: str,
    region_url: str,
    all_estates: list[str],
    known_urls: set[str],
) -> list[ListingDiscovery]:
    """
    Fetch a single regional catalog page once and return per-(estate, color) discoveries.

    Used for retailers whose catalogue is organised by appellation rather than
    by estate (e.g. labouteilledoree /en/pessac-leognan/).  One HTTP request
    covers all tracked estates and both colors.
    """
    discoveries: dict[tuple[str, str], ListingDiscovery] = {
        (estate, color): ListingDiscovery(
            retailer=retailer, estate=estate, catalog_url=region_url, wine_color=color
        )
        for estate in all_estates
        for color in ("Rouge", "Blanc")
    }
    try:
        resp = requests.get(region_url, headers=REQUESTS_HEADERS, timeout=20)
        if resp.status_code != 200:
            for d in discoveries.values():
                d.error = f"HTTP {resp.status_code}"
            return list(discoveries.values())

        soup = BeautifulSoup(resp.text, "html.parser")
        domain = region_url.split("/")[2]
        seen: set[str] = set()

        for a in soup.find_all("a", href=True):
            href: str = a["href"].strip()
            if href.startswith("/"):
                href = f"https://{domain}{href}"
            if not href.startswith("http"):
                continue
            href = href.split("#")[0].rstrip("/")
            if href in known_urls or href in seen:
                continue
            seen.add(href)
            link_text = a.get_text(" ", strip=True)
            for estate in all_estates:
                for color in ("Rouge", "Blanc"):
                    if _is_product_url(href, estate, link_text=link_text, wine_color=color):
                        disc = discoveries[(estate, color)]
                        if href not in disc.new_urls:
                            disc.new_urls.append(href)

        time.sleep(1.5)
    except Exception as exc:
        for d in discoveries.values():
            d.error = str(exc)

    return [d for d in discoveries.values() if d.new_urls or d.error]


def run_listing_crawl(products: list[KnownProduct]) -> list[ListingDiscovery]:
    known_urls = {p.url for p in products}
    all_estates = _all_estates(products)
    listing_pages = _build_all_listing_pages(products)

    results: list[ListingDiscovery] = []

    # Per-estate listing pages (auto-generated + manual)
    # Each crawl fetches the page once and returns discoveries for both colors.
    for (retailer, estate), catalog_url in sorted(listing_pages.items()):
        logger.info(f"  crawling {retailer} / {estate}")
        results.extend(crawl_listing_page(retailer, estate, catalog_url, known_urls))

    # Region listing pages — one fetch covers all estates and both colors
    for retailer, region_url in REGION_LISTING_PAGES.items():
        logger.info(f"  crawling region page {retailer} ({region_url})")
        results.extend(crawl_region_page(retailer, region_url, all_estates, known_urls))

    return results


# ---------------------------------------------------------------------------
# Strategy C: URL-pattern probing
# ---------------------------------------------------------------------------

@dataclass
class ProbeDiscovery:
    retailer: str
    estate: str
    wine_color: str = "Rouge"
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
    For each retailer in URL_BUILDERS, probe every (estate x vintage) pair
    not already in the CSV.

    Any retailer added to URL_BUILDERS is automatically probed against all
    estates currently in the CSV.  Any new estate added to the CSV is
    automatically probed on all URL_BUILDER retailers.
    """
    # URL probing is rouge-only — blanc slugs vary too much per retailer.
    # Keying on wine_color ensures we don't skip rouge probes for estates
    # that already have a blanc row for the same vintage.
    known_set: set[tuple[str, str, int, str]] = {
        (p.retailer, p.estate_name, p.vintage, p.wine_color) for p in products
    }
    all_estates = _all_estates(products)

    results: list[ProbeDiscovery] = []
    for retailer, (slug_fn, url_fn) in URL_BUILDERS.items():
        for estate in all_estates:
            slug = slug_fn(estate)
            if slug is None:
                continue  # estate not supported / not yet mapped for this retailer
            discovery = ProbeDiscovery(retailer=retailer, estate=estate, wine_color="Rouge")
            for vintage in PROBE_VINTAGES:
                if (retailer, estate, vintage, "Rouge") in known_set:
                    continue
                url = url_fn(slug, vintage)
                logger.info(f"  probing {retailer} / {estate} {vintage}")
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
    print("Retailers x estates where vintages are missing from master CSV")
    print("=" * 70)

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
        print(f"    [{g.retailer}] [{g.wine_color}]  missing recent: {recent}  |  known: {g.known_vintages}")


def print_listing_results(results: list[ListingDiscovery]) -> None:
    print("\n" + "=" * 70)
    print("LISTING-PAGE CRAWL RESULTS")
    print("New product URLs found on catalog pages (not in master CSV)")
    print("=" * 70)
    found_any = False
    for r in results:
        if r.error:
            logger.debug(f"  [{r.retailer}] {r.estate}: {r.error}")
            continue
        if r.new_urls:
            found_any = True
            print(f"\n  [{r.retailer}] {r.estate} [{r.wine_color}] -- {len(r.new_urls)} new URL(s):")
            for url in r.new_urls:
                print(f"    {url}")
    if not found_any:
        print("  No new URLs found on listing pages.")


def print_probe_results(results: list[ProbeDiscovery]) -> None:
    print("\n" + "=" * 70)
    print("URL-PATTERN PROBE RESULTS")
    print("New pages that respond 200 for missing vintages")
    print("=" * 70)
    if not results:
        print("  No new URLs found via URL probing.")
        return
    for r in results:
        print(f"\n  [{r.retailer}] {r.estate} [{r.wine_color}] -- {len(r.new_urls)} new URL(s):")
        for url in r.new_urls:
            print(f"    {url}")


def build_json_report(
    gaps: list[GapReport],
    listings: list[ListingDiscovery],
    probes: list[ProbeDiscovery],
    action_items: list[dict] | None = None,
) -> dict:
    return {
        "generated_at": date.today().isoformat(),
        "current_year": CURRENT_YEAR,
        "coverage_gaps": [
            {
                "retailer": g.retailer,
                "estate": g.estate,
                "wine_color": g.wine_color,
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
        "action_items": (
            action_items if action_items is not None
            else _build_action_items(listings, probes)
        ),
    }


# Canonical estate names — kept in sync with load_master_products.ESTATE_NAME_CANONICAL.
# Defined here to avoid importing SQLAlchemy when url_discovery runs standalone.
ESTATE_NAME_CANONICAL: dict[str, str] = {
    "Chateau Malartic Lagraviere": "Chateau Malartic-Lagraviere",
    "Chateau Sociando Mallet":     "Chateau Sociando-Mallet",
}

# Default price selectors per retailer — used when auto-appending to CSV.
# Retailers that use a non-standard bottle_size string in master_products.csv.
# Used by auto_append_to_csv so new rows match existing rows for the unique constraint.
RETAILER_BOTTLE_SIZE: dict[str, str] = {
    "vinotheque_bordeaux": "75cl",
}

RETAILER_DEFAULTS: dict[str, str] = {
    "cercledemartillac":   "span.price",
    "vin_malin":           "span.price",
    "vintageandco":        "span.current-price-value",
    "cavissima":           "span.price-item__unit",
    "wineclub":            "span[itemprop='price']",
    "12bouteilles":        "span.prix_unit",
    "aries":               "span.product-unit-price span.font-weight-bold",
    "chateauinternet":     "div.price",
    "cashvin":             "p.price",
    "vinotheque_bordeaux": "span.price",
    "wineandco":           "span[itemprop='price']",
    "levindevantsoi":      "p.price",
    "labouteilledoree":    "div.current-price",
    "millesimes":          "span.price-current",
}


def _extract_vintage(url: str) -> int | None:
    """Return the first vintage year (2015-CURRENT_YEAR) found in the URL."""
    candidates = [int(m) for m in re.findall(r"\b(20[12]\d)\b", url)]
    valid = [y for y in candidates if 2015 <= y <= CURRENT_YEAR]
    return valid[0] if valid else None


def _build_action_items(
    listings: list[ListingDiscovery],
    probes: list[ProbeDiscovery],
) -> list[dict]:
    """Flatten all newly discovered URLs into a ready-to-act list."""
    items: list[dict] = []
    for r in listings:
        for url in r.new_urls:
            vintage = _extract_vintage(url)
            items.append({
                "action": "add_to_csv",
                "retailer": r.retailer,
                "estate": r.estate,
                "url": url,
                "vintage": vintage,
                "wine_color": r.wine_color,
                "price_selector": RETAILER_DEFAULTS.get(r.retailer, "span.price"),
                "source": "listing_page",
            })
    for r in probes:
        for url in r.new_urls:
            vintage = _extract_vintage(url)
            items.append({
                "action": "add_to_csv",
                "retailer": r.retailer,
                "estate": r.estate,
                "url": url,
                "vintage": vintage,
                "wine_color": r.wine_color,
                "price_selector": RETAILER_DEFAULTS.get(r.retailer, "div.price"),
                "source": "url_probe",
            })
    return items


# ---------------------------------------------------------------------------
# Auto-append to CSV
# ---------------------------------------------------------------------------

def auto_append_to_csv(items: list[dict]) -> int:
    """
    Append newly discovered URLs to master_products.csv.

    Skips items where:
    - vintage cannot be extracted from the URL
    - (retailer, estate_name, vintage) already exists in the CSV

    Returns the number of rows appended.
    """
    existing: set[tuple[str, str, int, str]] = set()
    with open(CSV_PATH, encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            try:
                v = int(row["vintage_start"])
            except (ValueError, KeyError):
                continue
            wc = (row.get("wine_color") or "").strip() or "Rouge"
            existing.add((row["retailer"].strip(), row["estate_name"].strip(), v, wc))

    new_rows: list[str] = []
    seen_this_run: set[tuple[str, str, int, str]] = set()

    for item in items:
        vintage = item.get("vintage")
        if vintage is None:
            logger.debug(f"auto-append: skipping {item['url']} -- no vintage")
            continue

        retailer   = item["retailer"]
        estate_raw = item["estate"]
        estate     = ESTATE_NAME_CANONICAL.get(estate_raw, estate_raw)
        wine_color = (item.get("wine_color") or "Rouge")
        key        = (retailer, estate, vintage, wine_color)

        if key in existing or key in seen_this_run:
            continue
        seen_this_run.add(key)

        selector    = item.get("price_selector") or RETAILER_DEFAULTS.get(retailer, "span.price")
        bottle_size = RETAILER_BOTTLE_SIZE.get(retailer, "0.75L")
        url         = item["url"]
        note        = f"Auto-discovered {date.today().isoformat()}"

        row = (
            f"{estate},{retailer},{url},,{selector},"
            f"{vintage},{vintage},{bottle_size},TRUE,{wine_color},{note}"
        )
        new_rows.append(row)
        logger.info(f"auto-append: +{retailer} / {estate} {vintage} [{wine_color}]  {url}")

    if new_rows:
        with open(CSV_PATH, "a", encoding="utf-8-sig", newline="\n") as f:
            f.write("\n".join(new_rows) + "\n")
        logger.info(f"auto-append: {len(new_rows)} row(s) written to {CSV_PATH}")
    else:
        logger.info("auto-append: nothing new to append")

    return len(new_rows)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Discover new wine product URLs")
    parser.add_argument("--gaps-only", action="store_true",
                        help="Print coverage gap table only (no HTTP requests)")
    parser.add_argument("--probe", action="store_true",
                        help="Also run URL-pattern probing (slow, ~200+ HTTP requests)")
    parser.add_argument("--no-json", action="store_true",
                        help="Skip writing url_discovery_report.json")
    parser.add_argument("--auto-append", action="store_true",
                        help="Automatically append discovered URLs to master_products.csv")
    args = parser.parse_args()

    print(f"\nURL Discovery -- {date.today()}  (tracking up to {CURRENT_YEAR})")

    logger.info("Loading master_products.csv...")
    products = load_known_products()
    estates = _all_estates(products)
    rouge_count = sum(1 for p in products if p.wine_color.lower() == "rouge")
    blanc_count = sum(1 for p in products if p.wine_color.lower() == "blanc")
    logger.info(f"  {len(products)} active entries ({rouge_count} rouge, {blanc_count} blanc) | {len(estates)} estates")

    # Strategy A: gap report (always runs, free)
    gaps = coverage_gap_report(products)
    print_gap_table(gaps)

    listings: list[ListingDiscovery] = []
    probes: list[ProbeDiscovery] = []

    if not args.gaps_only:
        # Strategy B: listing-page crawl (always runs with HTTP)
        logger.info(f"\nRunning listing-page crawl ({len(LISTING_URL_BUILDERS)} auto-builders"
                    f" x {len(estates)} estates + {len(MANUAL_LISTING_PAGES)} manual pages)...")
        listings = run_listing_crawl(products)
        print_listing_results(listings)

        # Strategy C: URL probing (opt-in — use --probe flag)
        if args.probe:
            retailer_list = list(URL_BUILDERS.keys())
            n_combos = len(retailer_list) * len(estates) * len(PROBE_VINTAGES)
            logger.info(
                f"\nRunning URL-pattern probing: {retailer_list} x "
                f"{len(estates)} estates x {len(PROBE_VINTAGES)} vintages "
                f"= up to {n_combos} probes (skipping known)..."
            )
            probes = run_url_probing(products)
            print_probe_results(probes)

    # Build action items
    action_items = _build_action_items(listings, probes)

    # Auto-append to CSV if requested
    appended = 0
    if args.auto_append and action_items:
        logger.info("\nAuto-appending discovered URLs to master_products.csv...")
        appended = auto_append_to_csv(action_items)

    # Summary
    all_new = [u for r in listings for u in r.new_urls] + \
              [u for r in probes for u in r.new_urls]
    print(f"\n{'=' * 70}")
    print(f"SUMMARY: {len(all_new)} new URL(s) found across all strategies.")
    if appended:
        print(f"  -> {appended} row(s) auto-appended to {CSV_PATH}")
    elif all_new and not args.auto_append:
        print("  -> Run with --auto-append to write these to master_products.csv")

    if not args.no_json:
        report = build_json_report(gaps, listings, probes, action_items)
        REPORT_PATH.write_text(
            json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        print(f"  -> Report written to {REPORT_PATH}")


if __name__ == "__main__":
    main()
