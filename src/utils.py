from decimal import Decimal
import re


def parse_price(raw_text: str):
    """
    Parses price strings like:
    - '39,20 €'
    - '€39.20'
    - '39.20 EUR'
    - '1.299,00 €'
    - '1,299.00 €'
    - '39.20'        ← no currency symbol, defaults to EUR
    Returns: (Decimal, currency_str)
    """
    if not raw_text:
        raise ValueError("Empty price text")

    text = raw_text.replace("\xa0", " ").strip()

    # Detect currency — default to EUR if none found (French wine sites)
    if "€" in text or "EUR" in text:
        currency = "EUR"
    elif "$" in text or "USD" in text:
        currency = "USD"
    elif "£" in text or "GBP" in text:
        currency = "GBP"
    else:
        currency = "EUR"  # FIX: default to EUR instead of UNKNOWN

    match = re.search(r"([\d.,]+)", text)
    if not match:
        raise ValueError(f"Could not parse price from: {raw_text!r}")

    number = match.group(1)

    if "," in number and "." in number:
        comma_pos = number.rfind(",")
        dot_pos   = number.rfind(".")
        if comma_pos > dot_pos:
            number = number.replace(".", "").replace(",", ".")
        else:
            number = number.replace(",", "")
    elif "," in number and "." not in number:
        number = number.replace(",", ".")

    price = Decimal(number)
    return price, currency
