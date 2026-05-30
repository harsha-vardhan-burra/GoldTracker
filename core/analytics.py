import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database.db_manager import get_price_history
import statistics


# ─── SIGNAL 1: Price vs 7-day Moving Average (30 pts) ────────────────────────
def score_vs_ma7(current_price, ma7):
    if not ma7 or not current_price:
        return 15, "insufficient data for 7-day average"

    diff_pct = ((current_price - ma7) / ma7) * 100

    if diff_pct > 3:
        return 0,  f"price is {diff_pct:.1f}% above 7-day average — overpriced short term"
    elif diff_pct > 1:
        return 8,  f"price is {diff_pct:.1f}% above 7-day average"
    elif diff_pct >= -1:
        return 15, f"price is near 7-day average (±1%)"
    elif diff_pct >= -3:
        return 23, f"price is {abs(diff_pct):.1f}% below 7-day average"
    else:
        return 30, f"price is {abs(diff_pct):.1f}% below 7-day average — good dip"


# ─── SIGNAL 2: Price vs 30-day Moving Average (30 pts) ───────────────────────
def score_vs_ma30(current_price, ma30):
    if not ma30 or not current_price:
        return 15, "insufficient data for 30-day average"

    diff_pct = ((current_price - ma30) / ma30) * 100

    if diff_pct > 3:
        return 0,  f"price is {diff_pct:.1f}% above 30-day average — elevated"
    elif diff_pct > 1:
        return 8,  f"price is {diff_pct:.1f}% above 30-day average"
    elif diff_pct >= -1:
        return 15, f"price is near 30-day average"
    elif diff_pct >= -3:
        return 23, f"price is {abs(diff_pct):.1f}% below 30-day average"
    else:
        return 30, f"price is {abs(diff_pct):.1f}% below 30-day average — historically low"


# ─── SIGNAL 3: Momentum (25 pts) ─────────────────────────────────────────────
def score_momentum(momentum):
    if momentum is None:
        return 12, "trend direction unavailable"

    if momentum > 0.5:
        return 0,  "price rising fast — consider waiting"
    elif momentum > 0.1:
        return 8,  "price trending upward"
    elif momentum >= -0.1:
        return 15, "price is stable"
    elif momentum >= -0.5:
        return 20, "downward trend detected — potential buy window"
    else:
        return 25, "price falling sharply — strong buy signal"


# ─── SIGNAL 4: Volatility (15 pts) ───────────────────────────────────────────
def score_volatility(volatility):
    if volatility is None:
        return 7, "volatility unknown"

    if volatility > 300:
        return 0,  "high volatility — signal less reliable"
    elif volatility > 150:
        return 5,  "moderate volatility"
    elif volatility > 50:
        return 10, "low-moderate volatility"
    else:
        return 15, "price is stable — signal reliable"


# ─── COMPUTE MOVING AVERAGES ──────────────────────────────────────────────────
def compute_ma(prices, window):
    if len(prices) < window:
        return None
    return round(sum(prices[-window:]) / window, 2)


# ─── COMPUTE MOMENTUM ────────────────────────────────────────────────────────
def compute_momentum(prices, window=5):
    if len(prices) < window + 1:
        return None
    recent = prices[-(window):]
    changes = [
        ((recent[i] - recent[i-1]) / recent[i-1]) * 100
        for i in range(1, len(recent))
    ]
    return round(sum(changes) / len(changes), 4)


# ─── COMPUTE VOLATILITY ───────────────────────────────────────────────────────
def compute_volatility(prices, window=10):
    if len(prices) < window:
        return None
    recent = prices[-window:]
    return round(statistics.stdev(recent), 2)


# ─── BUILD EXPLANATION STRING ─────────────────────────────────────────────────
def build_explanation(reasons):
    # Filter out generic/filler reasons
    filtered = [r for r in reasons if 'unavailable' not in r
                                   and 'unknown' not in r
                                   and 'insufficient' not in r]
    if not filtered:
        return "Not enough historical data yet — check back after a few hours"
    return " · ".join(filtered)


# ─── METER LABEL ─────────────────────────────────────────────────────────────
def get_buy_label(score):
    if score >= 75:
        return "PERFECT TIME TO BUY"
    elif score >= 55:
        return "GOOD TIME TO BUY"
    elif score >= 35:
        return "WAIT A BIT MORE"
    else:
        return "BAD TIME TO BUY"


def get_sell_label(score):
    inverted = 100 - score
    if inverted >= 75:
        return "PERFECT TIME TO SELL"
    elif inverted >= 55:
        return "GOOD TIME TO SELL"
    elif inverted >= 35:
        return "HOLD FOR NOW"
    else:
        return "BAD TIME TO SELL"


# ─── MAIN ANALYTICS FUNCTION ─────────────────────────────────────────────────
def run_analytics(current_price):
    """
    Takes current 24K price in INR.
    Reads history from DB, computes all signals,
    returns a dict ready to be merged into price data.
    """

    # Fetch last 30 days of history from DB
    history = get_price_history(days=30)
    prices  = [row['price_24k'] for row in history if row['price_24k']]

    # Compute indicators
    ma7        = compute_ma(prices, 7)
    ma30       = compute_ma(prices, 30)
    momentum   = compute_momentum(prices)
    volatility = compute_volatility(prices)

    # Score each signal
    s1, r1 = score_vs_ma7(current_price, ma7)
    s2, r2 = score_vs_ma30(current_price, ma30)
    s3, r3 = score_momentum(momentum)
    s4, r4 = score_volatility(volatility)

    buy_score  = s1 + s2 + s3 + s4
    sell_score = 100 - buy_score

    explanation = build_explanation([r1, r2, r3, r4])

    return {
        'ma7':         ma7,
        'ma30':        ma30,
        'momentum':    momentum,
        'volatility':  volatility,
        'buy_score':   buy_score,
        'sell_score':  sell_score,
        'explanation': explanation,
        'buy_label':   get_buy_label(buy_score),
        'sell_label':  get_sell_label(sell_score),
    }


# ─── Quick test ───────────────────────────────────────────────────────────────
if __name__ == '__main__':
    # Simulate with current price
    test_price = 13504.0
    result = run_analytics(test_price)

    print('\n' + '='*50)
    print('ANALYTICS RESULT')
    print('='*50)
    print(f"MA7          : {result['ma7']}")
    print(f"MA30         : {result['ma30']}")
    print(f"Momentum     : {result['momentum']}")
    print(f"Volatility   : {result['volatility']}")
    print(f"Buy Score    : {result['buy_score']}/100")
    print(f"Sell Score   : {result['sell_score']}/100")
    print(f"Buy Signal   : {result['buy_label']}")
    print(f"Sell Signal  : {result['sell_label']}")
    print(f"Explanation  : {result['explanation']}")
    print('='*50)