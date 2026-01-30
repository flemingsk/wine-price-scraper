import re
from playwright.sync_api import sync_playwright


class VinatisScraper:
    retailer = "vinatis"

    VINTAGE_REGEX = re.compile(r"(19|20)\d{2}")

    def scrape(self, url: str, price_selector: str):
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()
            page.goto(url, timeout=60000)
            page.wait_for_timeout(3000)

            # Price
            price_el = page.query_selector(price_selector)
            if not price_el:
                browser.close()
                raise RuntimeError("Price element not found")

            raw_price = price_el.inner_text().strip()

            # Availability (Vinatis almost always available if page loads)
            availability = True

            # Vintage extraction from page text
            page_text = page.inner_text("body")
            vintage = None
            match = self.VINTAGE_REGEX.search(page_text)
            if match:
                vintage = int(match.group())

            browser.close()

        return raw_price, availability, vintage
