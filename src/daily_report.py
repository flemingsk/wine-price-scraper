# src/daily_report.py
"""
Unified daily report — single 'daily_report' GSheet tab, refreshed every run.

Replaces: daily_reports, run_log, market_analysis, price_review tabs.

Sections:
  1. RUN HEALTH       — scrape counts, success rate, failures, missing vs yesterday
  2. PRICE FLAGS      — threshold violations + cross-retailer outliers needing review
  3. RETAILER SPREADS — best price vs 7-day median, filtered to >10% deviation
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
from .process_flags import FLAGS_TAB, FLAGS_HEADER, FLAGS_README, ISSUE_TYPES, STATUS_VALUES

logger = logging.getLogger(__name__)

REPORT_TAB = "daily_report"
OLD_TABS   = [
    "daily_reports", "run_log", "market_analysis", "price_review",
    "cavissima_blanc_audit", "alerts", "alert_latest",
]

NCOLS = 10   # all rows padded to this width

# ── Thresholds ────────────────────────────────────────────────────────────────
LOW_ROUGE          = 25.0
LOW_BLANC          = 18.0
HIGH_PRICE         = 70.0
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


def _ensure_flags_tab(sheet):
    """Get or create the 'flags' worksheet with the correct header row."""
    try:
        ws = sheet.worksheet(FLAGS_TAB)
        existing = ws.get_all_values()
        if not existing:
            ws.append_row(FLAGS_HEADER)
        elif existing[0] != FLAGS_HEADER:
            # Header is stale (column order changed) — overwrite row 1 in place.
            ws.update("A1", [FLAGS_HEADER])
    except gspread.exceptions.WorksheetNotFound:
        ws = sheet.add_worksheet(title=FLAGS_TAB, rows=2000, cols=26)
        ws.append_row(FLAGS_HEADER)
    # Expand columns if tab was created before the README sidebar was added
    if ws.col_count < 26:
        ws.resize(rows=max(ws.row_count, 2000), cols=26)
    _write_flags_readme(ws)
    _set_flags_validation(ws)
    return ws


def _write_flags_readme(ws) -> None:
    """Write the README sidebar to column N, always overwriting so it stays current."""
    # Column N = 14th column; data occupies A-L (12 cols), M is a spacer
    readme_data = [[line] for line in FLAGS_README]
    readme_data += [[""] for _ in range(10)]  # clear any previously longer README
    ws.update(f"N1:N{len(readme_data)}", readme_data)


def _set_flags_validation(ws) -> None:
    """Apply dropdown validation on issue_type and status columns. Run on every setup."""
    issue_col  = FLAGS_HEADER.index("issue_type")
    status_col = FLAGS_HEADER.index("status")
    requests = []
    for col_idx, values in [(issue_col, ISSUE_TYPES), (status_col, STATUS_VALUES)]:
        requests.append({
            "setDataValidation": {
                "range": {
                    "sheetId": ws.id,
                    "startRowIndex": 1,   # skip header row
                    "endRowIndex": 2000,
                    "startColumnIndex": col_idx,
                    "endColumnIndex": col_idx + 1,
                },
                "rule": {
                    "condition": {
                        "type": "ONE_OF_LIST",
                        "values": [{"userEnteredValue": v} for v in values],
                    },
                    "showCustomUi": True,
                    "strict": False,  # allow system-written values like "auto-detected"
                },
            }
        })
    ws.spreadsheet.batch_update({"requests": requests})


def _load_validated(ws) -> set:
    """
    Return set of (estate_name, retailer, vintage_str) tuples whose status='validated'.
    These are permanently suppressed from Section 2. Delete the row to re-enable.
    """
    rows = ws.get_all_values()
    if len(rows) < 2:
        return set()
    header = rows[0]
    col = {name: idx for idx, name in enumerate(header)}
    suppressed = set()
    for row in rows[1:]:
        row = list(row) + [""] * (len(header) - len(row))
        if row[col.get("status", -1)].strip().lower() != "validated":
            continue
        estate   = row[col["estate_name"]].strip() if "estate_name" in col else ""
        retailer = row[col["retailer"]].strip()     if "retailer"    in col else ""
        vintage  = row[col["vintage"]].strip()      if "vintage"     in col else ""
        if estate and retailer:
            suppressed.add((estate, retailer, vintage))
    return suppressed


def _write_detected_flags(ws, detected_items: list[dict], today) -> None:
    """Append auto-detected price flags to the flags tab, deduped by (url, date)."""
    if not detected_items:
        return
    today_str = str(today)
    existing = ws.get_all_values()
    header = existing[0] if existing else FLAGS_HEADER
    url_col  = header.index("current_url")   if "current_url"   in header else None
    date_col = header.index("date_flagged")  if "date_flagged"  in header else None
    existing_keys = set()
    if url_col is not None and date_col is not None:
        for row in existing[1:]:
            if len(row) > max(url_col, date_col):
                existing_keys.add((row[url_col], row[date_col]))
    new_rows = []
    for item in detected_items:
        url = item.get("current_url", "")
        if (url, today_str) in existing_keys:
            continue
        new_rows.append([
            today_str, "auto-detected",
            item.get("estate_name", ""), item.get("retailer", ""),
            str(item.get("vintage", "")), item.get("price", ""),
            "",              # G: issue_type — pick from dropdown
            "",              # H: corrected_url — fill in if needed
            url,             # I: current_url (informational, auto-filled)
            item.get("notes", ""),
            "auto-detected", # K: status — change to 'pending' to trigger processing
            "",
        ])
    if new_rows:
        ws.append_rows(new_rows, value_input_option="USER_ENTERED")
        logger.info(f"Wrote {len(new_rows)} auto-detected flags to '{FLAGS_TAB}'")


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

def _price_flags(today, suppressed: set = frozenset()) -> tuple[list[list], int, list[dict]]:
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
    detected_items = []

    for _, r in df.iterrows():
        price    = float(r["price_amount"])
        estate   = r["estate_name"]
        color    = r["wine_color"] or "Rouge"
        vintage  = "" if pd.isna(r["vintage"]) else int(r["vintage"])
        retailer = r["retailer"]
        median   = float(r["median_price"]) if pd.notna(r.get("median_price")) else None
        ratio    = float(r["ratio"]) if pd.notna(r.get("ratio")) else None
        n_ret    = int(r["n_retailers"]) if pd.notna(r.get("n_retailers")) else 0
        url      = r["url"] or ""

        if (estate, retailer, str(vintage)) in suppressed:
            continue

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
        detected_items.append({
            "estate_name": estate,
            "retailer": retailer,
            "vintage": vintage,
            "price": f"€{price:.2f}",
            "current_url": url,
            "notes": " | ".join(issues),
        })

    if flag_count == 0:
        rows.append(_pad(["✓ No price flags today — all prices within expected range."]))

    return rows, flag_count, detected_items


# ── Section 3: Retailer Spreads vs 7-day Median ──────────────────────────────

SPREAD_VS_MEDIAN_MIN = 0.10   # only show if either extreme deviates >10% from 7d median

def _retailer_spreads(today) -> list[list]:
    yesterday = today - timedelta(days=1)
    week_ago  = today - timedelta(days=7)

    df = pd.read_sql(
        """
        WITH hist_7d AS (
            SELECT mp.estate_name, pr.vintage, mp.wine_color,
                   PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY pr.price_amount) AS median_7d
            FROM price_records pr
            JOIN master_products mp ON mp.id = pr.master_product_id
            WHERE DATE(pr.fetched_at AT TIME ZONE 'UTC') BETWEEN %(week_ago)s AND %(yesterday)s
              AND pr.price_amount IS NOT NULL AND pr.price_amount > 0
            GROUP BY mp.estate_name, pr.vintage, mp.wine_color
        )
        SELECT mp.estate_name, mp.wine_color, pr.vintage, mp.retailer,
               pr.price_amount, pr.url, h.median_7d
        FROM price_records pr
        JOIN master_products mp ON mp.id = pr.master_product_id
        LEFT JOIN hist_7d h ON h.estate_name = mp.estate_name
                           AND h.vintage IS NOT DISTINCT FROM pr.vintage
                           AND h.wine_color = mp.wine_color
        WHERE DATE(pr.fetched_at AT TIME ZONE 'UTC') = %(today)s
          AND pr.price_amount IS NOT NULL AND pr.price_amount > 0
        """,
        engine, params={"today": today, "yesterday": yesterday, "week_ago": week_ago},
    )

    rows = [_cols("Estate", "Color", "Vintage", "7d Median €",
                  "Best Price €", "Best Retailer", "vs Median %",
                  "Priciest €", "Priciest Retailer", "Best URL")]

    if df.empty:
        rows.append(_pad(["No data for today."]))
        return rows

    groups = []
    for (estate, color, vintage), grp in df.groupby(["estate_name", "wine_color", "vintage"], dropna=False):
        if grp["retailer"].nunique() < MIN_RETAILERS:
            continue
        median_7d = grp["median_7d"].iloc[0]
        if pd.isna(median_7d) or float(median_7d) <= 0:
            continue  # no historical reference to compare against

        min_row = grp.loc[grp["price_amount"].idxmin()]
        max_row = grp.loc[grp["price_amount"].idxmax()]
        min_p = float(min_row["price_amount"])
        max_p = float(max_row["price_amount"])
        med   = float(median_7d)

        min_vs_med = (min_p - med) / med * 100   # negative = cheaper than median
        max_vs_med = (max_p - med) / med * 100   # positive = pricier than median
        sort_key   = max(abs(min_vs_med), abs(max_vs_med))

        if sort_key <= SPREAD_VS_MEDIAN_MIN * 100:
            continue

        vintage_str = "" if pd.isna(vintage) else int(vintage)
        groups.append((
            estate, color, vintage_str,
            med, min_p, min_row["retailer"], min_row["url"] or "",
            min_vs_med,
            max_p, max_row["retailer"],
            sort_key,
        ))

    groups.sort(key=lambda x: x[10], reverse=True)

    for g in groups:
        estate, color, vintage, med, min_p, cheap, cheap_url, min_vs_med, max_p, pricey, _ = g
        rows.append(_pad([
            estate, color, vintage,
            f"€{med:.2f}",
            f"€{min_p:.2f}", cheap, f"{min_vs_med:+.1f}%",
            f"€{max_p:.2f}", pricey,
            cheap_url,
        ]))

    if not groups:
        rows.append(_pad(["No products with >10% spread vs 7-day median today."]))

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

def export_daily_report(
    start_time: datetime,
    duration_seconds: float,
    flags_summary: dict | None = None,
) -> None:
    today     = start_time.date()
    generated = start_time.strftime("%Y-%m-%d %H:%M UTC")

    client = _get_gsheet_client()
    sheet  = client.open(GOOGLE_SHEET_NAME)

    _delete_old_tabs(sheet)
    ws_flags = _ensure_flags_tab(sheet)

    try:
        ws = sheet.worksheet(REPORT_TAB)
        ws.clear()
    except gspread.exceptions.WorksheetNotFound:
        ws = sheet.add_worksheet(title=REPORT_TAB, rows=2000, cols=NCOLS)

    health_rows, stats   = _run_health(today, start_time, duration_seconds)
    if flags_summary:
        p, m, e = flags_summary["processed"], flags_summary["needs_manual"], flags_summary["errors"]
        health_rows.append(_pad([
            "Flags processed this run", p,
            f"{m} need manual action | {e} error(s)" if (m or e) else "all auto-applied",
        ]))

    suppressed = _load_validated(ws_flags)
    flag_rows, n_flags, detected_items = _price_flags(today, suppressed)
    _write_detected_flags(ws_flags, detected_items, today)
    spread_rows        = _retailer_spreads(today)
    trend_rows         = _price_trends(today)
    opp_rows           = _buying_opps(today)

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
        _section("SECTION 3 — RETAILER SPREAD vs 7-DAY MEDIAN (>10% deviation, sorted by spread)"),
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
