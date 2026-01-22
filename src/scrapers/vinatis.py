from playwright.sync_api import sync_playwright
from decimal import Decimal
import re


def scrape_vinatis_price(url: str, selector: str):
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(url, timeout=30000)

        page.wait_for_selector(selector, timeout=15000)
        raw = page.locator(selector).inner_text()

        browser.close()

    match = re.search(r"([\d,.]+)", raw)
    if not match:
        return None, None, raw, False

    amount = Decimal(match.group(1).replace(",", "."))
    return amount, "EUR", raw, True
