import requests
from bs4 import BeautifulSoup
from tenacity import retry, stop_after_attempt, wait_exponential
from decimal import Decimal
from datetime import datetime, timezone

def fetch_price_millesima(product_url):
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
    }

    try:
        response = requests.get(product_url, headers=headers)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")

        price_span = soup.find("span", class_="ProductPrice_offer-price__rVXIa")
        if price_span:
            raw = price_span.get_text(strip=True)
            price_text = raw.replace("\xa0€", "").replace(",", ".")
            price_amount = float(price_text)
            currency = "EUR"
            availability = True
        else:
            raw = None
            price_amount = None
            currency = None
            availability = False

    except requests.HTTPError:
        raw = None
        price_amount = None
        currency = None
        availability = False

    return {
        "site": "millesima",
        "url": product_url,
        "price_amount": price_amount,
        "currency": currency,
        "raw_price_text": raw,
        "availability": availability,
        "note": None,
        "fetched_at": datetime.now(timezone.utc)
    }

if __name__ == "__main__":
    url = "https://www.millesima.fr/chateau-latour-martillac-2019.html"
    print(fetch_price_millesima(url))