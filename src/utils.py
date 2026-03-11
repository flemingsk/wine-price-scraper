from decimal import Decimal
import re


def parse_price(raw_text: str):
    """
    Parses price strings like:
    - '39,20 €'
    - '€39.20'
    - '39.20 EUR'
    - '1.299,00 €'   <-- FIX (ISSUE 2): European thousands separator now handled
    - '1,299.00 €'   <-- UK/US thousands separator also handled
    Returns: (Decimal, currency_str)
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

    # Extract numeric part (digits, dots, commas)
    match = re.search(r"([\d.,]+)", text)
    if not match:
        raise ValueError(f"Could not parse price from: {raw_text!r}")

    number = match.group(1)

    # FIX (ISSUE 2): Handle all European/UK price formats correctly
    #
    # Cases:
    #   '1.299,00'  -> European: dot=thousands, comma=decimal -> '1299.00'
    #   '1,299.00'  -> UK/US:    comma=thousands, dot=decimal -> '1299.00'
    #   '39,20'     -> European simple: comma=decimal         -> '39.20'
    #   '39.20'     -> Standard: already correct              -> '39.20'
    #   '1299'      -> Integer: no separator                  -> '1299'

    if "," in number and "." in number:
        # Determine which is the decimal separator by position
        comma_pos = number.rfind(",")
        dot_pos = number.rfind(".")
        if comma_pos > dot_pos:
            # European format: '1.299,00' — comma is decimal
            number = number.replace(".", "").replace(",", ".")
        else:
            # UK/US format: '1,299.00' — dot is decimal
            number = number.replace(",", "")
    elif "," in number and "." not in number:
        # Simple European: '39,20'
        number = number.replace(",", ".")
    # else: already a clean number ('39.20', '1299')

    price = Decimal(number)
    return price, currency
