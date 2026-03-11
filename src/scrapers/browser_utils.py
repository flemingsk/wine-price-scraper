"""
Shared browser utilities for Playwright-based scrapers.

Provides a reusable browser context with:
- Realistic User-Agent rotation
- Random delays between requests (polite scraping)
- Anti-bot evasion (navigator.webdriver removal, realistic viewport)
- Stealth headers matching a real Chrome browser
"""
import random
import time
import logging

logger = logging.getLogger(__name__)

# Realistic User-Agents — rotate to reduce fingerprinting
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
]

# Realistic viewports
VIEWPORTS = [
    {"width": 1920, "height": 1080},
    {"width": 1440, "height": 900},
    {"width": 1280, "height": 800},
    {"width": 1366, "height": 768},
]

# Realistic request headers — mimic a real Chrome browser
STEALTH_HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
    "Accept-Language": "fr-FR,fr;q=0.9,en-US;q=0.8,en;q=0.7",
    "Accept-Encoding": "gzip, deflate, br",
    "DNT": "1",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
}

# requests-compatible headers (for static scrapers)
REQUESTS_HEADERS = {
    "User-Agent": random.choice(USER_AGENTS),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "fr-FR,fr;q=0.9,en-US;q=0.8",
    "Accept-Encoding": "gzip, deflate, br",
    "DNT": "1",
    "Connection": "keep-alive",
}


def polite_delay(min_s: float = 2.0, max_s: float = 5.0):
    """Sleep a random amount between requests to avoid rate limiting."""
    delay = random.uniform(min_s, max_s)
    logger.debug(f"Polite delay: {delay:.1f}s")
    time.sleep(delay)


def get_playwright_context(playwright):
    """
    Launch a Playwright browser with anti-detection settings.
    Returns (browser, context) — caller must close both.
    """
    ua = random.choice(USER_AGENTS)
    viewport = random.choice(VIEWPORTS)

    browser = playwright.chromium.launch(
        headless=True,
        args=[
            "--no-sandbox",
            "--disable-blink-features=AutomationControlled",
            "--disable-dev-shm-usage",
        ],
    )

    context = browser.new_context(
        user_agent=ua,
        viewport=viewport,
        extra_http_headers=STEALTH_HEADERS,
        locale="fr-FR",
    )

    # Remove navigator.webdriver flag — primary bot detection signal
    context.add_init_script("""
        Object.defineProperty(navigator, 'webdriver', {
            get: () => undefined
        });
    """)

    return browser, context
