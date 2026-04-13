# src/price_validator.py
"""
Sanity-check scraped prices against per-(retailer, estate, vintage) historical
medians before persisting to the database.

Problem being solved
--------------------
Some retailers expose a *case* price (6 or 12 bottles) where only the unit
bottle price is wanted.  Without a check, those inflated values corrupt the
historical series and trigger spurious price-spike alerts.

Correction logic
----------------
1. Query the historical median price for (master_product_id, vintage) over all
   previously stored price_records where price_amount > 0.

2. If the new price exceeds  median * RATIO_THRESHOLD  (default 3.5×):
   - Try dividing by 6, then 12.
   - Accept the first divisor whose result falls within CORRECTION_BAND of
     the median (default: 30%–300% of median).
   - If neither divisor produces a plausible unit price, log a warning and
     save the raw value as-is — some wines are genuinely expensive.

3. No historical data (first-ever record for this combo):
   - Apply a global ceiling (GLOBAL_MAX_EUR, default 500 €).
   - If the price exceeds the ceiling, attempt the same /6 /12 correction.
   - If correction fails, save as-is with a warning — it may be a legitimate
     premium price we haven't seen before.

Why not a hard cap?
-------------------
A fixed threshold (e.g. "flag everything above 81 €") would incorrectly
reject valid prices for premium Bordeaux vintages.  The median-relative
approach is self-calibrating: the threshold adapts to each estate/retailer.
"""

from __future__ import annotations

import dataclasses
import logging

from sqlalchemy import func
from sqlalchemy.orm import Session

from src.models import PriceRecord
from src.scrapers.base import ScrapeResult

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Tuning constants
# ---------------------------------------------------------------------------

CASE_SIZES = (6, 12)

# Price must exceed  median * RATIO_THRESHOLD  to be flagged as a potential
# case price.  3.5× prevents false positives for legitimate premium vintages
# (even a 2× year-on-year increase would not trigger).
RATIO_THRESHOLD = 3.5
RATIO_THRESHOLD_LOWER = 2.0  # Flag 2× outliers for aggressive correction attempt

# After dividing by a case size the corrected price must sit within this band
# around the historical median to be accepted.
CORRECTION_BAND_LO = 0.30   # floor: 30% of median
CORRECTION_BAND_HI = 3.00   # ceiling: 300% of median

# Fallback global ceiling used when there is no historical data yet.
# 500 € covers the upper end of tracked Bordeaux estates at 75cl.
GLOBAL_MAX_EUR = 500.0


# ---------------------------------------------------------------------------
# Median query
# ---------------------------------------------------------------------------

def _historical_median(
    master_product_id: int,
    vintage: int,
    session: Session,
) -> float | None:
    """
    Return the median price_amount for (master_product_id, vintage) across
    all stored price_records, or None if no records exist yet.

    Uses PostgreSQL PERCENTILE_CONT(0.5) for an exact continuous median.
    """
    result = (
        session.query(
            func.percentile_cont(0.5).within_group(PriceRecord.price_amount)
        )
        .filter(
            PriceRecord.master_product_id == master_product_id,
            PriceRecord.vintage == vintage,
            PriceRecord.price_amount.isnot(None),
            PriceRecord.price_amount > 0,
        )
        .scalar()
    )
    return float(result) if result is not None else None


# ---------------------------------------------------------------------------
# Core validator
# ---------------------------------------------------------------------------

def _try_case_correction(
    price: float,
    median: float,
) -> tuple[float, int] | None:
    """
    Try dividing price by each CASE_SIZE.  Return (corrected_price, divisor)
    for the first divisor that brings the price into CORRECTION_BAND of the
    median, or None if no correction is viable.
    """
    lo = median * CORRECTION_BAND_LO
    hi = median * CORRECTION_BAND_HI
    for divisor in CASE_SIZES:
        candidate = price / divisor
        if lo <= candidate <= hi:
            return candidate, divisor
    return None


def validate_price(
    result: ScrapeResult,
    master_product_id: int,
    session: Session,
) -> tuple[ScrapeResult, dict]:
    """
    Return (possibly corrected ScrapeResult, metadata dict).

    Metadata dict contains:
      - 'corrected' (bool): whether a correction was applied
      - 'original_price' (float or None): price before correction
      - 'reason' (str or None): explanation of correction

    Correction is applied when the scraped price appears to be a case total
    rather than a unit bottle price.  All corrections and warnings are logged.
    """
    metadata = {"corrected": False, "original_price": None, "reason": None}

    if result.price_amount is None or result.price_amount <= 0:
        return result, metadata
    price = float(result.price_amount)   # Decimal from parse_price() → float for arithmetic

    label = f"{result.retailer} | {result.vintage}"

    # ── Step 1: query historical median ──────────────────────────────────────
    median = _historical_median(master_product_id, result.vintage, session)

    # ── Step 2: median-relative check ────────────────────────────────────────
    if median is not None:
        ratio = price / median
        # Use aggressive threshold (2×) when we have established history,
        # since a 2× deviation on a well-tracked product is almost always a case/format error
        threshold = RATIO_THRESHOLD_LOWER if median > 20 else RATIO_THRESHOLD

        if ratio > threshold:
            correction = _try_case_correction(price, median)
            if correction:
                corrected, divisor = correction
                logger.warning(
                    f"[price_validator] {label}: {price:.2f} is {ratio:.1f}x median "
                    f"({median:.2f}) — corrected to {corrected:.2f} (/{divisor})"
                )
                metadata = {
                    "corrected": True,
                    "original_price": price,
                    "reason": f"case price ÷{divisor} ({ratio:.1f}x median)"
                }
                return dataclasses.replace(
                    result,
                    price_amount=round(corrected, 2),
                    raw_price_text=f"{result.raw_price_text} [corrected /{divisor}]",
                ), metadata
            else:
                logger.warning(
                    f"[price_validator] {label}: {price:.2f} is {ratio:.1f}x median "
                    f"({median:.2f}) — no case-size correction fit; saving as-is"
                )
        return result, metadata

    # ── Step 3: no historical data — use global ceiling ──────────────────────
    if price > GLOBAL_MAX_EUR:
        correction = _try_case_correction(price, GLOBAL_MAX_EUR / 2)
        if correction:
            corrected, divisor = correction
            logger.warning(
                f"[price_validator] {label}: {price:.2f} exceeds global ceiling "
                f"({GLOBAL_MAX_EUR:.0f}) with no history — corrected to "
                f"{corrected:.2f} (/{divisor})"
            )
            metadata = {
                "corrected": True,
                "original_price": price,
                "reason": f"exceeds global ceiling ÷{divisor}"
            }
            return dataclasses.replace(
                result,
                price_amount=round(corrected, 2),
                raw_price_text=f"{result.raw_price_text} [corrected /{divisor}]",
            ), metadata
        else:
            logger.warning(
                f"[price_validator] {label}: {price:.2f} exceeds global ceiling "
                f"({GLOBAL_MAX_EUR:.0f}) with no history — no correction fit; saving as-is"
            )

    return result, metadata
