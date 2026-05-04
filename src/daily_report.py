# src/daily_report.py
"""
Unified daily report — single 'daily_report' GSheet tab, refreshed every run.

Replaces: daily_reports, run_log, market_analysis, price_review tabs.

Sections:
  1. RUN HEALTH       — scrape counts, success rate, failures, missing vs yesterday
  2. PRICE FLAGS      — threshold violations + cross-retailer outliers needing review
  3. RETAILER SPREADS — cheapest vs most expensive per wine today (arbitrage view)
  4. PRICE TRENDS 7D  — movers > 5% over the past 7 days
  5. BUYING OPPS      — top 10 wines priced ≥10% below their 30-day market median
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

import gspread
import pandas as pd
from sqlalchemy import text

from .db import engine
from .export_to_gsheet import _get_gsheet_client, GOOGLE_SHEET_NAME

logger = logging.getLogger(__name__)

REPORT_TAB   = "daily_report"
APPROVED_TAB = "price_review_approved"
OLD_TABS     = ["daily_reports", "run_log", "market_analysis", "price_review"]

NCOLS = 10   # all rows padded to this width

# ── Thresholds ────────────────────────────────────────────────────────────────
LOW_ROUGE          = 25.0
LOW_BLANC          = 18.0
HIGH_PRICE         = 70.0
FAR_DEVIATION      = 3.0   # re-flag approved SKU if ratio exceeds this
OUTLIER_HIGH       = 1.50  # flag if price > 1.5× cross-retailer median
OUTLIER_LOW        = 0.67  # flag if price < 0.67× cross-retailer median
BUYING_OPP_MIN_DIS = 0.10  # at least 10% below 30-day median
MIN_RETAILERS      = 2     # minimum retailers to trust a median
MIN_PRICE_FLOOR    = 15.0  # ignore suspiciously low prices in buying opps

EXEMPT_ESTATES = {"Chateau Lespault-Martillac", "Chateau La Louviere"}


# ── Formatting helpers ────────────────────────────────────────────────────────

def _pad(row: list) -> list:
    return (list(row) + [""] * NCOLS)[:NCOLS]

def _blank() -> list:
    return [""] * NCOLS

def _section(title: str) -> list:
    return _pad([title])

def _cols(*headers) -> list:
    return _pad(list(headers))


# ── GSheet helpers ────────────────────────────────────────────────────────────

def _delete_old_tabs(sheet) -> None:
    for name in OLD_TABS:
        try:
            ws = sheet.worksheet(name)
            sheet.del_worksheet(ws)
            logger.info(f"Deleted old tab '{name}'")
        except gspread.exceptions.WorksheetNotFound:
            pass
        except Exception as exc:
            logger.warning(f"Could not delete tab '{name}': {exc}")


def _load_approved(sheet) -> set[tuple]:
    try:
        ws = sheet.worksheet(APPROVED_TAB)
    except gspread.exceptions.WorksheetNotFound:
        return set()

    rows = ws.get_all_values()
    if len(rows) < 2:
        return set()

    headers = [h.strip().lower() for h in rows[0]]
    try:
        ie = headers.index("estate_name")
        ir = headers.index("retailer")
        iv = headers.index("vintage")
    except ValueError:
        return set()

    approved = set()
    for row in rows[1:]:
        try:
            e = row[ie].strip()
            r = row[ir].strip()
            v = int(row[iv])
            if e and r and v:
                approved.add((e, r, v))
        except (ValueError, IndexError):
            continue
    return approved


# ── Section 1: Run Health ─────────────────────────────────────────────────────

def _run_health(today, start_time: datetime, duration_seconds: float) -> tuple[list[list], dict]:
    mins, secs = divmod(int(duration_seconds), 60)
    duration_str = f"{mins}m {secs}s"
    ts = start_time.strftime("%Y-%m-%d %H:%M:%S UTC")

    master_count = pd.read_sql(
        "SELECT COUNT(*) AS n FROM master_products WHERE active = TRUE AND bottle_size IN ('0.75L','75cl')",
        engine,
    ).iloc[0]["n"]

    scraped_count = pd.read_sql(
        "SELECT COUNT(DISTINCT master_product_id) AS n FROM price_records "
        "WHERE DATE(fetched_at AT TIME ZONE 'UTC') = %(today)s AND price_amount IS NOT NULL",
        engine, params={"today": today},
    ).iloc[0]["n"]

    corrections = pd.read_sql(
        "SELECT COUNT(*) AS n FROM price_records "
        "WHERE DATE(fetched_at AT TIME ZONE 'UTC') = %(today)s AND price_corrected = TRUE",
        engine, params={"today": today},
    ).iloc[0]["n"]

    success_pct = round(scraped_count / master_count * 100, 1) if master_count else 0

    missing_df = pd.read_sql(
        """
        WITH prev_date AS (
            SELECT MAX(DATE(fetched_at AT TIME ZONE 'UTC')) AS d
            FROM price_records
            WHERE DATE(fetched_at AT TIME ZONE 'UTC') < %(today)s AND price_amount IS NOT NULL
        ),
        prev AS (
            SELECT DISTINCT ON (pr.master_product_id, pr.vintage)
                pr.master_product_id, pr.vintage, pr.url
            FROM price_records pr CROSS JOIN prev_date
            WHERE DATE(pr.fetched_at AT TIME ZONE 'UTC') = prev_date.d AND pr.price_amount IS NOT NULL
            ORDER BY pr.master_product_id, pr.vintage
        ),
        today AS (
            SELECT DISTINCT master_product_id, vintage FROM price_records
            WHERE DATE(fetched_at AT TIME ZONE 'UTC') = %(today)s AND price_amount IS NOT NULL
        )
        SELECT mp.estate_name, mp.wine_color, COALESCE(p.vintage::text,'NV') AS vintage,
               mp.retailer, p.url
        FROM prev p
        JOIN master_products mp ON mp.id = p.master_product_id
        LEFT JOIN today t ON t.master_product_id = p.master_product_id
                         AND t.vintage IS NOT DISTINCT FROM p.vintage
        WHERE t.master_product_id IS NULL
        ORDER BY mp.estate_name, mp.retailer
        """,
        engine, params={"today": today},
    )

    rows = [
        _cols("Metric", "Value", "Detail"),
        _pad(["Run timestamp", ts]),
        _pad(["Duration", duration_str]),
        _pad(["Active products", int(master_count)]),
        _pad(["Successfully scraped", int(scraped_count), f"{success_pct}% success rate"]),
        _pad(["Missing vs yesterday", len(missing_df)]),
        _pad(["Price corrections applied", int(corrections)]),
    ]

    if not missing_df.empty:
        rows.append(_blank())
        rows.append(_cols("Missing products:", "Color", "Vintage", "Retailer", "Last seen URL"))
        for _, r in missing_df.iterrows():
            rows.append(_pad(["", r["estate_name"], r["wine_color"], r["vintage"], r["retailer"], r["url"]]))

    stats = {
        "scraped": int(scraped_count),
        "master": int(master_count),
        "success_pct": success_pct,
        "missing": len(missing_df),
        "corrections": int(corrections),
    }
    return rows, stats


# ── Section 2: Price Flags ────────────────────────────────────────────────────

def _price_flags(today, approved: set) -> tuple[list[list], int]:
    df = pd.read_sql(
        """
        WITH hist AS (
            SELECT mp.estate_name, pr.vintage, mp.wine_color,
                   PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY pr.price_amount) AS median_price,
                   COUNT(DISTINCT mp.retailer) AS n_retailers
            FROM price_records pr
            JOIN master_products mp ON mp.id = pr.master_product_id
            WHERE DATE(pr.fetched_at AT TIME ZONE 'UTC') < %(today)s
              AND pr.price_amount IS NOT NULL AND pr.price_amount > 0
            GROUP BY mp.estate_name, pr.vintage, mp.wine_color
        )
        SELECT mp.estate_name, mp.wine_color, pr.vintage, mp.retailer,
               pr.price_amount, pr.url,
               h.median_price, h.n_retailers,
               CASE WHEN h.median_price > 0
                    THEN ROUND((pr.price_amount / h.median_price)::numeric, 2)
                    ELSE NULL END AS ratio
        FROM price_records pr
        JOIN master_products mp ON mp.id = pr.master_product_id
        LEFT JOIN hist h ON h.estate_name = mp.estate_name
                        AND h.vintage IS NOT DISTINCT FROM pr.vintage
                        AND h.wine_color = mp.wine_color
        WHERE DATE(pr.fetched_at AT TIME ZONE 'UTC') = %(today)s
          AND pr.price_amount IS NOT NULL AND pr.price_amount > 0
        ORDER BY mp.estate_name, pr.vintage
        """,
        engine, params={"today": today},
    )

    rows = [_cols("Estate", "Color", "Vintage", "Retailer", "Price €", "Hist. Median €",
                  "Ratio", "Retailers", "Issue", "URL")]
    flag_count = 0

    for _, r in df.iterrows():
        price   = float(r["price_amount"])
        estate  = r["estate_name"]
        color   = r["wine_color"] or "Rouge"
        vintage = "" if pd.isna(r["vintage"]) else int(r["vintage"])
        retailer = r["retailer"]
        median  = float(r["median_price"]) if pd.notna(r.get("median_price")) else None
        ratio   = float(r["ratio"]) if pd.notna(r.get("ratio")) else None
        n_ret   = int(r["n_retailers"]) if pd.notna(r.get("n_retailers")) else 0
        url     = r["url"] or ""

        issues = []
        low_threshold = LOW_BLANC if color == "Blanc" else LOW_ROUGE
        if price < low_threshold and estate not in EXEMPT_ESTATES:
            issues.append(f"Below {low_threshold:.0f}€ threshold")
        if price > HIGH_PRICE:
            issues.append(f"Above {HIGH_PRICE:.0f}€ threshold")
        if ratio is not None and n_ret >= MIN_RETAILERS:
            if ratio > OUTLIER_HIGH:
                issues.append(f"{ratio:.1f}× hist. median")
            elif ratio < OUTLIER_LOW:
                issues.append(f"{ratio:.1f}× hist. median")

        if not issues:
            continue

        # Suppress approved items unless ratio is extreme
        approved_key = (estate, retailer, vintage if vintage != "" else None)
        if approved_key in approved:
            if ratio is None or ratio <= FAR_DEVIATION:
                continue
            issues = [f"APPROVED but {ratio:.1f}× median — re-flagged"] + issues

        flag_count += 1
        rows.append(_pad([
            estate, color, vintage, retailer,
            f"€{price:.2f}",
            f"€{median:.2f}" if median else "—",
            f"{ratio:.2f}" if ratio else "—",
            n_ret if n_ret else "—",
            " | ".join(issues),
            url,
        ]))

    if flag_count == 0:
        rows.append(_pad(["✓ No price flags today — all prices within expected range."]))

    return rows, flag_count


# ── Section 3: Retailer Spreads ───────────────────────────────────────────────

def _retailer_spreads(today) -> list[list]:
    df = pd.read_sql(
        """
        SELECT mp.estate_name, mp.wine_color, pr.vintage, mp.retailer, pr.price_amount, pr.url
        FROM price_records pr
        JOIN master_products mp ON mp.id = pr.master_product_id
        WHERE DATE(pr.fetched_at AT TIME ZONE 'UTC') = %(today)s
          AND pr.price_amount IS NOT NULL AND pr.price_amount > 0
        """,
        engine, params={"today": today},
    )

    rows = [_cols("Estate", "Color", "Vintage", "Min €", "Cheapest Retailer", "Cheapest URL",
                  "Max €", "Priciest Retailer", "Spread %", "N Retailers")]

    if df.empty:
        rows.append(_pad(["No data for today."]))
        return rows

    groups = []
    for (estate, color, vintage), grp in df.groupby(["estate_name", "wine_color", "vintage"], dropna=False):
        if len(grp["retailer"].unique()) < MIN_RETAILERS:
            continue
        min_row  = grp.loc[grp["price_amount"].idxmin()]
        max_row  = grp.loc[grp["price_amount"].idxmax()]
        min_p    = float(min_row["price_amount"])
        max_p    = float(max_row["price_amount"])
        spread   = round((max_p - min_p) / min_p * 100, 1) if min_p > 0 else 0
        n        = grp["retailer"].nunique()
        vintage_str = "" if pd.isna(vintage) else int(vintage)
        groups.append((estate, color, vintage_str, min_p, min_row["retailer"], min_row["url"] or "",
                       max_p, max_row["retailer"], spread, n))

    groups.sort(key=lambda x: x[8], reverse=True)

    for g in groups:
        estate, color, vintage, min_p, cheap, cheap_url, max_p, pricey, spread, n = g
        rows.append(_pad([estate, color, vintage,
                          f"€{min_p:.2f}", cheap, cheap_url,
                          f"€{max_p:.2f}", pricey,
                          f"{spread:.1f}%", n]))

    if len(groups) == 0:
        rows.append(_pad(["No products with ≥2 retailer prices today."]))

    return rows


# ── Section 4: Price Trends 7D ────────────────────────────────────────────────

def _price_trends(today, days: int = 7, min_pct: float = 5.0) -> list[list]:
    ref_date = today - timedelta(days=days)

    df = pd.read_sql(
        """
        WITH ref AS (
            SELECT DISTINCT ON (pr.master_product_id, pr.vintage)
                pr.master_product_id, pr.vintage, pr.price_amount AS ref_price
            FROM price_records pr
            WHERE DATE(pr.fetched_at AT TIME ZONE 'UTC')
                  BETWEEN %(ref_date)s AND %(ref_date)s + INTERVAL '2 days'
              AND pr.price_amount IS NOT NULL AND pr.price_amount > 0
            ORDER BY pr.master_product_id, pr.vintage, pr.fetched_at DESC
        ),
        today AS (
            SELECT DISTINCT ON (pr.master_product_id, pr.vintage)
                pr.master_product_id, pr.vintage, pr.price_amount AS today_price, pr.url
            FROM price_records pr
            WHERE DATE(pr.fetched_at AT TIME ZONE 'UTC') = %(today)s
              AND pr.price_amount IS NOT NULL AND pr.price_amount > 0
            ORDER BY pr.master_product_id, pr.vintage, pr.fetched_at DESC
        )
        SELECT mp.estate_name, mp.wine_color, t.vintage, mp.retailer,
               r.ref_price, t.today_price, t.url,
               ROUND(((t.today_price - r.ref_price) / r.ref_price * 100)::numeric, 1) AS pct_change
        FROM today t
        JOIN ref r ON r.master_product_id = t.master_product_id
                  AND r.vintage IS NOT DISTINCT FROM t.vintage
        JOIN master_products mp ON mp.id = t.master_product_id
        WHERE ABS((t.today_price - r.ref_price) / r.ref_price) >= %(min_pct)s / 100.0
          AND t.today_price <> r.ref_price
        ORDER BY ABS((t.today_price - r.ref_price) / r.ref_price) DESC
        """,
        engine,
        params={"today": today, "ref_date": ref_date, "min_pct": min_pct},
    )

    rows = [_cols("Estate", "Color", "Vintage", "Retailer",
                  f"Price {days}d Ago €", "Price Today €", "Δ%", "Direction", "URL")]

    if df.empty:
        rows.append(_pad([f"No price changes > {min_pct:.0f}% in the past {days} days."]))
        return rows

    for _, r in df.iterrows():
        vintage = "" if pd.isna(r["vintage"]) else int(r["vintage"])
        pct     = float(r["pct_change"])
        rows.append(_pad([
            r["estate_name"], r["wine_color"], vintage, r["retailer"],
            f"€{float(r['ref_price']):.2f}",
            f"€{float(r['today_price']):.2f}",
            f"{pct:+.1f}%",
            "↑" if pct > 0 else "↓",
            r["url"] or "",
        ]))

    return rows


# ── Section 5: Buying Opportunities ──────────────────────────────────────────

def _buying_opps(today, lookback_days: int = 30, top_n: int = 10) -> list[list]:
    ref_start = today - timedelta(days=lookback_days)

    df = pd.read_sql(
        """
        WITH hist AS (
            SELECT mp.estate_name, pr.vintage, mp.wine_color,
                   PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY pr.price_amount) AS median_price,
                   COUNT(DISTINCT mp.retailer) AS n_retailers
            FROM price_records pr
            JOIN master_products mp ON mp.id = pr.master_product_id
            WHERE DATE(pr.fetched_at AT TIME ZONE 'UTC') BETWEEN %(ref_start)s AND %(yesterday)s
              AND pr.price_amount IS NOT NULL AND pr.price_amount > 0
            GROUP BY mp.estate_name, pr.vintage, mp.wine_color
            HAVING COUNT(DISTINCT mp.retailer) >= %(min_ret)s
        ),
        today AS (
            SELECT DISTINCT ON (pr.master_product_id, pr.vintage)
                mp.estate_name, mp.wine_color, mp.retailer,
                pr.vintage, pr.price_amount, pr.url
            FROM price_records pr
            JOIN master_products mp ON mp.id = pr.master_product_id
            WHERE DATE(pr.fetched_at AT TIME ZONE 'UTC') = %(today)s
              AND pr.price_amount IS NOT NULL AND pr.price_amount > %(min_price)s
            ORDER BY pr.master_product_id, pr.vintage, pr.fetched_at DESC
        )
        SELECT t.estate_name, t.wine_color, t.vintage, t.retailer,
               t.price_amount AS today_price,
               h.median_price,
               h.n_retailers,
               ROUND(((h.median_price - t.price_amount) / h.median_price * 100)::numeric, 1) AS discount_pct,
               t.url
        FROM today t
        JOIN hist h ON h.estate_name = t.estate_name
                   AND h.vintage IS NOT DISTINCT FROM t.vintage
                   AND h.wine_color = t.wine_color
        WHERE t.price_amount < h.median_price * (1 - %(min_dis)s)
        ORDER BY discount_pct DESC
        LIMIT %(top_n)s
        """,
        engine,
        params={
            "today": today,
            "yesterday": today - timedelta(days=1),
            "ref_start": ref_start,
            "min_ret": MIN_RETAILERS,
            "min_price": MIN_PRICE_FLOOR,
            "min_dis": BUYING_OPP_MIN_DIS,
            "top_n": top_n,
        },
    )

    rows = [_cols("Estate", "Color", "Vintage", "Retailer",
                  "Today €", f"{lookback_days}d Median €", "Discount %",
                  "N Retailers", "URL")]

    if df.empty:
        rows.append(_pad(["No buying opportunities meeting criteria today."]))
        return rows

    for _, r in df.iterrows():
        vintage = "" if pd.isna(r["vintage"]) else int(r["vintage"])
        rows.append(_pad([
            r["estate_name"], r["wine_color"], vintage, r["retailer"],
            f"€{float(r['today_price']):.2f}",
            f"€{float(r['median_price']):.2f}",
            f"-{float(r['discount_pct']):.1f}%",
            int(r["n_retailers"]),
            r["url"] or "",
        ]))

    return rows


# ── Main export ───────────────────────────────────────────────────────────────

def export_daily_report(start_time: datetime, duration_seconds: float) -> None:
    today     = start_time.date()
    generated = start_time.strftime("%Y-%m-%d %H:%M UTC")

    client = _get_gsheet_client()
    sheet  = client.open(GOOGLE_SHEET_NAME)

    _delete_old_tabs(sheet)

    try:
        ws = sheet.worksheet(REPORT_TAB)
        ws.clear()
    except gspread.exceptions.WorksheetNotFound:
        ws = sheet.add_worksheet(title=REPORT_TAB, rows=2000, cols=NCOLS)

    approved = _load_approved(sheet)

    health_rows, stats  = _run_health(today, start_time, duration_seconds)
    flag_rows, n_flags  = _price_flags(today, approved)
    spread_rows         = _retailer_spreads(today)
    trend_rows          = _price_trends(today)
    opp_rows            = _buying_opps(today)

    # ── Narrative headline ────────────────────────────────────────────────────
    best_opp = ""
    if len(opp_rows) > 1 and opp_rows[1][0]:  # first data row
        r = opp_rows[1]
        best_opp = f" · Best value: {r[0]} {r[2]} @ {r[3]} ({r[6]} vs 30d median)"

    headline = (
        f"{stats['scraped']}/{stats['master']} scraped ({stats['success_pct']}%) "
        f"· {n_flags} price flag(s) · {stats['missing']} missing vs yesterday"
        f"{best_opp}"
    )

    # ── Assemble all rows ─────────────────────────────────────────────────────
    all_rows = [
        _pad([f"DAILY WINE PRICE REPORT — {generated}"]),
        _pad([headline]),
        _blank(),
        _section("SECTION 1 — RUN HEALTH"),
        *health_rows,
        _blank(),
        _section("SECTION 2 — PRICE FLAGS — REVIEW REQUIRED"),
        *flag_rows,
        _blank(),
        _section("SECTION 3 — RETAILER SPREAD TODAY (sorted by spread %, min 2 retailers)"),
        *spread_rows,
        _blank(),
        _section("SECTION 4 — PRICE TRENDS — 7 DAYS (changes > 5%)"),
        *trend_rows,
        _blank(),
        _section("SECTION 5 — TOP 10 BUYING OPPORTUNITIES (≥10% below 30-day market median)"),
        *opp_rows,
    ]

    ws.append_rows(all_rows, value_input_option="USER_ENTERED")

    # ── Bold section headers and column header rows ───────────────────────────
    bold = {"textFormat": {"bold": True}}
    bold_rows = [1, 4]  # report title and S1 header (1-indexed)
    cursor = 4 + len(health_rows) + 2   # S2 header
    bold_rows.append(cursor)
    bold_rows.append(cursor + 1)        # S2 column headers
    cursor += len(flag_rows) + 2        # S3 header
    bold_rows.append(cursor)
    bold_rows.append(cursor + 1)
    cursor += len(spread_rows) + 2      # S4 header
    bold_rows.append(cursor)
    bold_rows.append(cursor + 1)
    cursor += len(trend_rows) + 2       # S5 header
    bold_rows.append(cursor)
    bold_rows.append(cursor + 1)

    for row_idx in bold_rows:
        try:
            ws.format(f"A{row_idx}:J{row_idx}", bold)
        except Exception:
            pass

    print(
        f"Daily report written to '{REPORT_TAB}': "
        f"{stats['scraped']}/{stats['master']} scraped, "
        f"{n_flags} flags, {stats['missing']} missing.",
        flush=True,
    )
