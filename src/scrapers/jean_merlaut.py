import requests
from bs4 import BeautifulSoup
from tenacity import retry, stop_after_attempt, wait_exponential
from decimal import Decimal
import datetime, re

HEADERS = {"User-Agent": "PriceAgent/1.0 (+mailto:you@example.com)"}
URL = "https://jean-merlaut.com/catalogue-des-vins/2708-chateau-latour-martillac-rouge-2019.html"

def _parse_price_text(raw):
    if not raw:
        return None, None
    txt = raw.replace('\u00a0', ' ').strip()
    m = re.search(r'([\d\.,]+)\s*€', txt)
    if m:
        num = m.group(1)
        num = num.replace(' ', '').replace(u'\u202f','')
        if num.count(',') == 1 and num.count('.') == 0:
            num = num.replace(',', '.')
        else:
            num = num.replace(',', '')
        try:
            return Decimal(num), 'EUR'
        except:
            return None, None
    return None, None

@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
def fetch_price(session=None):
    session = session or requests.Session()
    r = session.get(URL, headers=HEADERS, timeout=15)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "lxml")
    # Inspect typical structure: price often in .price or .tt-price
    price_el = soup.select_one(".price, .tt-price, .product-price, .price-value")
    raw = price_el.get_text(strip=True) if price_el else None
    price_amount, currency = _parse_price_text(raw)
    availability = None
    avail_el = soup.select_one(".availability, .stock")
    if avail_el:
        availability = avail_el.get_text(strip=True)
    return {
        "site": "jean-merlaut",
        "url": URL,
        "price_amount": price_amount,
        "currency": currency,
        "raw_price_text": raw,
        "availability": availability,
        "note": None,
        "fetched_at": datetime.datetime.utcnow()
    }
