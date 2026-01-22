from decimal import Decimal
import re


def parse_price(raw_text: str):
    """
    Parses price strings like:
    - '39,20 €'
    - '€39.20'
    - '39.20 EUR'
    Returns: (Decimal, currency)
    """
    if not raw_text:
        raise ValueError("Empty price text")

    text = raw_text.replace("\xa0", " ").strip()

    # Detect currency
    if "€" in text or "EUR" in text:
        currency = "EUR"
    elif "$" in text or "USD" in text:
        currency = "USD"
    else:
        currency = "UNKNOWN"

    # Extract numeric part
    match = re.search(r"([\d.,]+)", text)
    if not match:
        raise ValueError(f"Could not parse price from: {raw_text}")

    number = match.group(1)

    # European format handling
    if "," in number and "." not in number:
        number = number.replace(",", ".")

    price = Decimal(number)

    return price, currency
