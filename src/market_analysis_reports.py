"""
Market Analysis Reports — Track key market signals and generate recurring insights.

Reports track:
1. Price trends (up/down per estate per retailer)
2. Retailer spreads (price variance between retailers for same product)
3. Availability changes (products going in/out of stock)
4. Price outliers (unusual spikes/drops)
5. Market momentum (estates with consistent trend)

Can be exported to:
- CSV for spreadsheet analysis
- JSON for dashboard integration
- Google Sheets via gspread
"""

from datetime import datetime, timedelta
from collections import defaultdict
from statistics import mean, stdev
from src.db import SessionLocal
from src.models import MasterProduct, PriceRecord
from sqlalchemy.orm import joinedload
import json
import csv

class MarketAnalyzer:
    def __init__(self, lookback_days=7):
        self.db = SessionLocal()
        self.lookback_days = lookback_days
        self.cutoff_date = datetime.now() - timedelta(days=lookback_days)

    def close(self):
        self.db.close()

    def get_recent_prices(self):
        """Get all prices from the last N days, eager-loading products to avoid N+1."""
        return (
            self.db.query(PriceRecord)
            .options(joinedload(PriceRecord.product))
            .filter(PriceRecord.fetched_at >= self.cutoff_date)
            .all()
        )

    def calculate_price_trends(self):
        """Analyze price trends per estate/retailer"""
        recent = self.get_recent_prices()

        # Group by estate+retailer+vintage, then by date
        by_ref = defaultdict(lambda: defaultdict(list))

        for price in recent:
            product = price.product
            key = (product.estate_name, product.retailer, price.vintage)
            date_key = price.fetched_at.date()

            if price.price_amount:
                by_ref[key][date_key].append(float(price.price_amount))

        trends = []

        for (estate, retailer, vintage), daily_prices in by_ref.items():
            if len(daily_prices) < 2:
                continue  # Need at least 2 days of data

            dates = sorted(daily_prices.keys())

            # Calculate daily averages
            daily_avgs = [
                (date, mean(daily_prices[date]))
                for date in dates
            ]

            first_price = daily_avgs[0][1]
            last_price = daily_avgs[-1][1]
            change = last_price - first_price
            change_pct = (change / first_price * 100) if first_price else 0

            # Determine trend direction
            if change_pct > 2:
                direction = "UP"
            elif change_pct < -2:
                direction = "DOWN"
            else:
                direction = "STABLE"

            trends.append({
                'estate': estate,
                'retailer': retailer,
                'vintage': vintage,
                'direction': direction,
                'change_pct': round(change_pct, 2),
                'first_price': round(first_price, 2),
                'last_price': round(last_price, 2),
                'days_of_data': len(dates),
                'date_range': f"{dates[0]} to {dates[-1]}"
            })

        return sorted(trends, key=lambda x: abs(x['change_pct']), reverse=True)

    def calculate_retailer_spreads(self):
        """Analyze price variance between retailers for same product"""
        recent = self.get_recent_prices()

        # Group by estate+vintage
        by_estate_vintage = defaultdict(lambda: defaultdict(list))

        for price in recent:
            product = price.product
            key = (product.estate_name, price.vintage)

            if price.price_amount:
                by_estate_vintage[key][product.retailer].append(float(price.price_amount))

        spreads = []

        for (estate, vintage), retailers_prices in by_estate_vintage.items():
            if len(retailers_prices) < 2:
                continue  # Need at least 2 retailers

            # Calculate average price per retailer
            retailer_avgs = {
                retailer: mean(prices)
                for retailer, prices in retailers_prices.items()
                if prices
            }

            if not retailer_avgs:
                continue

            min_price = min(retailer_avgs.values())
            max_price = max(retailer_avgs.values())
            spread_pct = (max_price - min_price) / min_price * 100

            # Find cheapest and most expensive retailers
            cheapest = min(retailer_avgs.items(), key=lambda x: x[1])
            most_expensive = max(retailer_avgs.items(), key=lambda x: x[1])

            spreads.append({
                'estate': estate,
                'vintage': vintage,
                'spread_pct': round(spread_pct, 2),
                'min_price': round(min_price, 2),
                'max_price': round(max_price, 2),
                'cheapest': cheapest[0],
                'most_expensive': most_expensive[0],
                'num_retailers': len(retailer_avgs)
            })

        return sorted(spreads, key=lambda x: x['spread_pct'], reverse=True)

    def calculate_availability_changes(self):
        """Track products going in/out of stock"""
        recent = self.get_recent_prices()

        # Group by estate+retailer+vintage
        by_ref = defaultdict(list)

        for price in recent:
            product = price.product
            key = (product.estate_name, product.retailer, price.vintage)
            by_ref[key].append({
                'date': price.fetched_at.date(),
                'available': price.availability is not False,  # Treat None as available
                'price': price.price_amount
            })

        changes = []

        for (estate, retailer, vintage), records in by_ref.items():
            if len(records) < 2:
                continue

            # Check if availability changed
            first_available = records[0]['available']
            last_available = records[-1]['available']

            if first_available != last_available:
                status_change = "DELISTED" if first_available and not last_available else "RELISTED"
                changes.append({
                    'estate': estate,
                    'retailer': retailer,
                    'vintage': vintage,
                    'status_change': status_change,
                    'date_range': f"{records[0]['date']} to {records[-1]['date']}",
                    'records': len(records)
                })

        return sorted(changes, key=lambda x: x['status_change'])

    def identify_price_outliers(self):
        """Flag unusual price spikes or drops"""
        recent = self.get_recent_prices()

        # Group by estate+retailer+vintage
        by_ref = defaultdict(list)

        for price in recent:
            product = price.product
            key = (product.estate_name, product.retailer, price.vintage)
            if price.price_amount:
                by_ref[key].append(float(price.price_amount))

        outliers = []

        for (estate, retailer, vintage), prices in by_ref.items():
            if len(prices) < 3:
                continue  # Need at least 3 data points

            avg = mean(prices)
            std_dev = stdev(prices) if len(prices) > 1 else 0

            if std_dev == 0:
                continue  # No variance

            # Find outliers (>2 std deviations from mean)
            for price in prices:
                z_score = abs(price - avg) / std_dev
                if z_score > 2:
                    deviation_pct = abs(price - avg) / avg * 100
                    outliers.append({
                        'estate': estate,
                        'retailer': retailer,
                        'vintage': vintage,
                        'outlier_price': round(price, 2),
                        'expected_price': round(avg, 2),
                        'deviation_pct': round(deviation_pct, 2),
                        'z_score': round(z_score, 2)
                    })

        return sorted(outliers, key=lambda x: x['deviation_pct'], reverse=True)

    def generate_daily_report(self):
        """Generate a daily market analysis report"""
        print(f"\n{'='*80}")
        print(f"DAILY MARKET ANALYSIS REPORT")
        print(f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"Period: Last {self.lookback_days} days")
        print(f"{'='*80}\n")

        # Price Trends
        print("\n1. PRICE TRENDS (Top 10 movers)")
        print("-" * 80)
        trends = self.calculate_price_trends()
        for trend in trends[:10]:
            symbol = "UP " if trend['direction'] == 'UP' else ("DN " if trend['direction'] == 'DOWN' else "-- ")
            print(f"{symbol} {trend['estate']} {trend['vintage']} @ {trend['retailer']}")
            print(f"   {trend['first_price']} -> {trend['last_price']} ({trend['change_pct']:+.2f}%) over {trend['days_of_data']} days")

        # Retailer Spreads
        print("\n2. PRICE SPREADS (Top 10 highest variance)")
        print("-" * 80)
        spreads = self.calculate_retailer_spreads()
        for spread in spreads[:10]:
            print(f"{spread['estate']} {spread['vintage']}: {spread['spread_pct']:.1f}% spread")
            print(f"   Cheapest: {spread['cheapest']} @ €{spread['min_price']}")
            print(f"   Most expensive: {spread['most_expensive']} @ €{spread['max_price']}")

        # Availability Changes
        print("\n3. AVAILABILITY CHANGES")
        print("-" * 80)
        changes = self.calculate_availability_changes()
        delistings = [c for c in changes if c['status_change'] == 'DELISTED']
        relistings = [c for c in changes if c['status_change'] == 'RELISTED']

        if delistings:
            print(f"DELISTINGS: {len(delistings)}")
            for d in delistings[:5]:
                print(f"  • {d['estate']} {d['vintage']} @ {d['retailer']}")

        if relistings:
            print(f"RELISTINGS: {len(relistings)}")
            for r in relistings[:5]:
                print(f"  • {r['estate']} {r['vintage']} @ {r['retailer']}")

        # Price Outliers
        print("\n4. PRICE OUTLIERS (Unusual spikes/drops)")
        print("-" * 80)
        outliers = self.identify_price_outliers()
        for outlier in outliers[:10]:
            print(f"{outlier['estate']} {outlier['vintage']} @ {outlier['retailer']}")
            print(f"   Expected: €{outlier['expected_price']}, Got: €{outlier['outlier_price']} ({outlier['deviation_pct']:+.1f}%)")

        print(f"\n{'='*80}\n")

    def export_json(self, filename='market_analysis.json'):
        """Export all analysis to JSON"""
        report = {
            'generated_at': datetime.now().isoformat(),
            'lookback_days': self.lookback_days,
            'price_trends': self.calculate_price_trends(),
            'retailer_spreads': self.calculate_retailer_spreads(),
            'availability_changes': self.calculate_availability_changes(),
            'price_outliers': self.identify_price_outliers()
        }

        with open(filename, 'w') as f:
            json.dump(report, f, indent=2, default=str)

        return filename

    def export_csv(self, filename_prefix='market_analysis'):
        """Export analysis to multiple CSV files"""
        files = []

        # Trends CSV
        trends_file = f'{filename_prefix}_trends.csv'
        with open(trends_file, 'w', newline='') as f:
            writer = csv.DictWriter(f, fieldnames=['estate', 'retailer', 'vintage', 'direction', 'change_pct', 'first_price', 'last_price'])
            writer.writeheader()
            writer.writerows(self.calculate_price_trends())
        files.append(trends_file)

        # Spreads CSV
        spreads_file = f'{filename_prefix}_spreads.csv'
        with open(spreads_file, 'w', newline='') as f:
            writer = csv.DictWriter(f, fieldnames=['estate', 'vintage', 'spread_pct', 'min_price', 'max_price', 'cheapest', 'most_expensive'])
            writer.writeheader()
            writer.writerows(self.calculate_retailer_spreads())
        files.append(spreads_file)

        return files

if __name__ == "__main__":
    analyzer = MarketAnalyzer(lookback_days=7)
    try:
        analyzer.generate_daily_report()
        json_file = analyzer.export_json()
        csv_files = analyzer.export_csv()
        print(f"Exported JSON: {json_file}")
        print(f"Exported CSVs: {', '.join(csv_files)}")
    finally:
        analyzer.close()
