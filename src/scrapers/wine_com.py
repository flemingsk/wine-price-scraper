import requests
from bs4 import BeautifulSoup
from tenacity import retry, stop_after_attempt, wait_exponential
from decimal import Decimal
import datetime, re
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
import os, time

HEADERS = {"User-Agent": "PriceAgent/1.0 (+mailto:you@example.com)"}

def _parse_price_text(raw):
    if not raw:
        return None, None
    txt = raw.replace('\u00a0', ' ').strip()
    m = re.search(r'([\d\.,]+)\s*([€USD£€]*)', txt)
    if m:
        num = m.group(1)
        num = num.replace(' ', '').replace(u'\u202f','')
        if num.count(',') == 1 and num.count('.') == 0:
            num = num.replace(',', '.')
        else:
            num = num.replace(',', '')
        try:
            return Decimal(num), (m.group(2) or 'USD')
        except:
            return None, None
    return None, None

@retry(stop=stop_after_attempt(2), wait=wait_exponential(multiplier=1, min=2, max=8))
def fetch_price(session=None):
    session = session or requests.Session()
    product_url = "https://www.wine.com/..."  # replace with the product url for the 2019
    # try basic GET
    try:
        r = session.get(product_url, headers=HEADERS, timeout=15)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "lxml")
        # wine.com often has: <span class="prod-Price"> or <span data-test="product-price">
        price_el = soup.select_one('[data-test="product-price"], .prod-Price, .product-price')
        raw = price_el.get_text(strip=True) if price_el else None
        price_amount, currency = _parse_price_text(raw)
        if price_amount is not None:
            return {
                "site": "wine.com",
                "url": product_url,
                "price_amount": price_amount,
                "currency": currency,
                "raw_price_text": raw,
                "availability": None,
                "note": "parsed via requests",
                "fetched_at": datetime.datetime.utcnow()
            }
    except Exception:
        # fall through to selenium fallback
        pass

    # Selenium fallback
    try:
        chrome_options = Options()
        chrome_options.add_argument("--headless=new")
        chrome_options.add_argument("--no-sandbox")
        chrome_options.add_argument("--disable-dev-shm-usage")
        driver_path = os.getenv("CHROMEDRIVER_PATH", "/usr/bin/chromedriver")
        driver = webdriver.Chrome(options=chrome_options, executable_path=driver_path)
        driver.get(product_url)
        time.sleep(2)  # small wait; consider explicit waits
        # try to find price element
        elems = driver.find_elements("css selector", '[data-test="product-price"], .prod-Price, .product-price')
        raw = elems[0].text.strip() if elems else None
        price_amount, currency = _parse_price_text(raw)
        driver.quit()
        return {
            "site": "wine.com",
            "url": product_url,
            "price_amount": price_amount,
            "currency": currency,
            "raw_price_text": raw,
            "availability": None,
            "note": "parsed via selenium" if price_amount else "no price found",
            "fetched_at": datetime.datetime.utcnow()
        }
    except Exception as e:
        return {
            "site": "wine.com",
            "url": product_url,
            "price_amount": None,
            "currency": None,
            "raw_price_text": None,
            "availability": None,
            "note": f"error: {str(e)}",
            "fetched_at": datetime.datetime.utcnow()
        }
