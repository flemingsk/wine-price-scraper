"""
Analyze pricing data from last 2-3 days vs historical data to identify scraping issues.

This script identifies retailers that are systematically scraping incorrect prices by:
1. Comparing recent prices (last 2-3 days) against historical averages
2. Calculating price variance per retailer/estate/vintage
3. Flagging outliers and missing data
4. Generating a report of problematic retailers
"""

from datetime import datetime, timedelta, date
from collections import defaultdict
from decimal import Decimal
from statistics import median
from src.db import SessionLocal
from src.models import MasterProduct, PriceRecord

# Per-retailer baseline cutoff dates.
# Historical records BEFORE this date are excluded from comparison baselines
# because the scraper was producing wrong data (e.g. case totals instead of unit prices).
BASELINE_CUTOFFS = {
    "twil":   date(2026, 3, 28),  # Before fix: span#totalPrice (case total). After: span.price (unit)
    "dubecq": date(2026, 4, 14),  # Before fix: catalogue anchors returned uniform €25 default. After: direct product page URLs
    "aries":             date(2026, 4, 15),  # Before fix: 6-bottle case prices (~6x too high). Corrected in DB on 2026-04-15.
    "cercledemartillac": date(2026, 4, 15),  # Before fix: span.price returned 6-bottle case total. Fixed to [itemprop=price]. Corrected in DB on 2026-04-15.
}


def analyze_pricing_issues():
    db = SessionLocal()

    try:
        # Get date range: last 3 days
        today = datetime.now()
        three_days_ago = today - timedelta(days=3)

        print(f"\n{'='*80}")
        print(f"PRICING ANALYSIS: {three_days_ago.date()} to {today.date()}")
        print(f"{'='*80}\n")

        # Query all price records
        all_prices = db.query(PriceRecord).all()
        recent_prices = [
            p for p in all_prices
            if p.fetched_at.date() >= three_days_ago.date()
        ]

        print(f"Total records in DB: {len(all_prices)}")
        print(f"Recent records (last 3 days): {len(recent_prices)}\n")

        # Group by estate + retailer + vintage
        by_ref = defaultdict(lambda: {'recent': [], 'historical': []})

        for price in all_prices:
            product = price.product
            key = (product.estate_name, product.retailer, price.vintage)

            # Skip historical records before the retailer's baseline cutoff date
            cutoff = BASELINE_CUTOFFS.get(product.retailer)
            if cutoff and price.fetched_at.date() < cutoff and price.fetched_at.date() < three_days_ago.date():
                continue  # Pre-fix dirty data, exclude from historical baseline

            if price.fetched_at.date() >= three_days_ago.date():
                by_ref[key]['recent'].append(price)
            else:
                by_ref[key]['historical'].append(price)

        # Analyze each reference
        issues = []

        for (estate, retailer, vintage), data in sorted(by_ref.items(), key=lambda x: (x[0][0], x[0][1], x[0][2] or 0)):
            recent = data['recent']
            historical = data['historical']

            if not recent:
                continue  # No recent data

            # Calculate recent prices — distinguish NULL (scraper failure) from 0 (out of stock)
            recent_nulls    = [p for p in recent if p.price_amount is None]
            recent_zeroes   = [p for p in recent if p.price_amount is not None and p.price_amount == 0]
            recent_valid    = [float(p.price_amount) for p in recent if p.price_amount]

            if not recent_valid:
                if recent_zeroes:
                    issue_type = 'OUT_OF_STOCK'
                else:
                    issue_type = 'NO_PRICE_SCRAPED'
                issues.append({
                    'estate': estate,
                    'retailer': retailer,
                    'vintage': vintage,
                    'issue': issue_type,
                    'recent_count': len(recent),
                    'recent_avg': 0 if recent_zeroes else None,
                    'hist_avg': None,
                    'variance': None
                })
                continue

            recent_avg = sum(recent_valid) / len(recent_valid)

            if historical:
                # Use median (not mean) for historical baseline — resistant to outlier spikes
                hist_prices = [float(p.price_amount) for p in historical if p.price_amount]

                if hist_prices:
                    hist_median = median(hist_prices)

                    # Calculate variance vs median baseline
                    variance_pct = abs(recent_avg - hist_median) / hist_median * 100 if hist_median else 0

                    if variance_pct > 15:
                        issues.append({
                            'estate': estate,
                            'retailer': retailer,
                            'vintage': vintage,
                            'issue': 'PRICE_VARIANCE',
                            'recent_count': len(recent),
                            'recent_avg': round(recent_avg, 2),
                            'hist_avg': round(hist_median, 2),
                            'variance': round(variance_pct, 1),
                        })

        # Report by retailer
        by_retailer = defaultdict(list)
        for issue in issues:
            by_retailer[issue['retailer']].append(issue)

        print(f"RETAILERS WITH ISSUES (sorted by issue count):\n")
        for retailer in sorted(by_retailer.keys(), key=lambda r: len(by_retailer[r]), reverse=True):
            issue_list = by_retailer[retailer]
            print(f"\n{retailer.upper()}: {len(issue_list)} issues")
            print("-" * 70)

            no_price = [i for i in issue_list if i['issue'] == 'NO_PRICE_SCRAPED']
            oos      = [i for i in issue_list if i['issue'] == 'OUT_OF_STOCK']
            variance = [i for i in issue_list if i['issue'] == 'PRICE_VARIANCE']

            if no_price:
                print(f"  {len(no_price)} NO_PRICE_SCRAPED (scraper failure):")
                for i in no_price[:5]:
                    print(f"    • {i['estate']} {i['vintage']} ({i['recent_count']} attempts)")
                if len(no_price) > 5:
                    print(f"    ... and {len(no_price)-5} more")

            if oos:
                print(f"  {len(oos)} OUT_OF_STOCK (0,00€ on page):")
                for i in oos[:5]:
                    print(f"    • {i['estate']} {i['vintage']}")
                if len(oos) > 5:
                    print(f"    ... and {len(oos)-5} more")

            if variance:
                print(f"  {len(variance)} PRICE_VARIANCE (>15%):")
                for i in variance[:5]:
                    print(f"    • {i['estate']} {i['vintage']}: recent={i['recent_avg']}, hist={i['hist_avg']}, variance={i['variance']}%")
                if len(variance) > 5:
                    print(f"    ... and {len(variance)-5} more")

        # Summary by issue type
        print(f"\n{'='*80}")
        print("SUMMARY BY ISSUE TYPE:")
        print(f"{'='*80}")

        no_price_count = len([i for i in issues if i['issue'] == 'NO_PRICE_SCRAPED'])
        oos_count      = len([i for i in issues if i['issue'] == 'OUT_OF_STOCK'])
        variance_count = len([i for i in issues if i['issue'] == 'PRICE_VARIANCE'])

        print(f"  NO_PRICE_SCRAPED (scraper failure):   {no_price_count} products")
        print(f"  OUT_OF_STOCK (0,00€ on page):         {oos_count} products")
        print(f"  PRICE_VARIANCE >15% vs historical:    {variance_count} products")
        print(f"  TOTAL ISSUES: {len(issues)} products\n")

        # Detailed issue list (for CSV export or further analysis)
        return by_retailer, issues

    finally:
        db.close()

if __name__ == "__main__":
    by_retailer, issues = analyze_pricing_issues()

    # Optional: Export to CSV for analysis
    if issues:
        import csv
        fieldnames = ['estate', 'retailer', 'vintage', 'issue', 'recent_count', 'recent_avg', 'hist_avg', 'variance']  # hist_avg is actually hist_median
        with open('pricing_issues_report.csv', 'w', newline='') as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction='ignore')
            writer.writeheader()
            for issue in issues:
                writer.writerow(issue)
        print(f"Report exported to pricing_issues_report.csv")
