import sys
import os

def _project_root() -> str:
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

sys.path.append(_project_root())

from database.db_manager import get_latest_price, get_connection
import datetime


# ─── REALISTIC BOUNDS FOR 24K GOLD (INR/gram) ────────────────────────────────
MIN_REALISTIC_PRICE = 5000.0    # gold won't be below ₹5,000/gram
MAX_REALISTIC_PRICE = 30000.0   # gold won't be above ₹30,000/gram
MAX_CHANGE_PCT      = 8.0       # max 8% change between 5-min readings


# ─── ANOMALY LOG TABLE ────────────────────────────────────────────────────────
def initialize_anomaly_table():
    conn   = get_connection()
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS anomaly_log (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp       DATETIME DEFAULT CURRENT_TIMESTAMP,
            price_received  REAL,
            previous_price  REAL,
            change_pct      REAL,
            reason          TEXT,
            data_source     TEXT
        )
    ''')
    conn.commit()
    conn.close()


def log_anomaly(price_received, previous_price, change_pct, reason, source):
    try:
        conn   = get_connection()
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO anomaly_log (
                price_received, previous_price,
                change_pct, reason, data_source
            ) VALUES (?, ?, ?, ?, ?)
        ''', (price_received, previous_price, change_pct, reason, source))
        conn.commit()
        conn.close()
        print(f'[AnomalyDetector] Logged: {reason} — received ₹{price_received}')
    except Exception as e:
        print(f'[AnomalyDetector] Log error: {e}')


def get_anomaly_log(limit=20):
    conn   = get_connection()
    cursor = conn.cursor()
    cursor.execute('''
        SELECT * FROM anomaly_log
        ORDER BY timestamp DESC
        LIMIT ?
    ''', (limit,))
    rows = cursor.fetchall()
    conn.close()
    return [dict(row) for row in rows]


# ─── VALIDATION RULES ────────────────────────────────────────────────────────
def validate_price(price_24k, data_source='unknown'):
    """
    Validates a new price reading before it gets stored.
    Returns (is_valid, reason)
    """
    # Rule 1 — must exist and be positive
    if not price_24k or price_24k <= 0:
        return False, 'Price is zero or missing'

    # Rule 2 — must be within realistic bounds
    if price_24k < MIN_REALISTIC_PRICE:
        return False, f'Price ₹{price_24k} below minimum ₹{MIN_REALISTIC_PRICE}'

    if price_24k > MAX_REALISTIC_PRICE:
        return False, f'Price ₹{price_24k} above maximum ₹{MAX_REALISTIC_PRICE}'

    # Rule 3 — must not change too fast vs previous reading
    latest = get_latest_price()
    if latest and latest.get('price_24k'):
        prev_price = latest['price_24k']
        change_pct = abs((price_24k - prev_price) / prev_price) * 100

        if change_pct > MAX_CHANGE_PCT:
            log_anomaly(
                price_24k, prev_price,
                change_pct,
                f'Change of {change_pct:.1f}% exceeds {MAX_CHANGE_PCT}% threshold',
                data_source
            )
            return False, f'Price jumped {change_pct:.1f}% — exceeds {MAX_CHANGE_PCT}% threshold'

    return True, 'OK'


# ─── DATA QUALITY SCORE ──────────────────────────────────────────────────────
def get_data_quality(data):
    """
    Returns a quality label and score (0-100) based on:
    - How many sources are live
    - Whether price passed validation
    - Source reliability
    Returns (label, score, details)
    """
    score   = 100
    details = []
    sources_live = 0

    # Check spot price source
    source = data.get('data_source', 'unknown')
    if source == 'gold-api.com':
        sources_live += 1
        details.append('gold-api.com: live')
    elif source == 'goldapi.io':
        sources_live += 1
        details.append('GoldAPI.io: live (backup)')
    else:
        score -= 25
        details.append('Spot price: unavailable')

    # Check Frankfurter
    if data.get('usd_inr'):
        sources_live += 1
        details.append('Frankfurter: live')
    else:
        score -= 25
        details.append('Frankfurter: unavailable')

    # Check GoodReturns retail
    if data.get('retail_price'):
        sources_live += 1
        details.append('GoodReturns: live')
    else:
        score -= 15
        details.append('GoodReturns: unavailable')

    # Label
    if score >= 90:
        label = 'HIGH'
    elif score >= 70:
        label = 'MEDIUM'
    elif score >= 50:
        label = 'LOW'
    else:
        label = 'POOR'

    return label, score, details, sources_live


# ─── Quick test ───────────────────────────────────────────────────────────────
if __name__ == '__main__':
    initialize_anomaly_table()
    print('Testing anomaly detector...\n')

    # Test 1 — valid price
    valid, reason = validate_price(13920.0, 'gold-api.com')
    print(f'Test 1 (valid price):     {valid} — {reason}')

    # Test 2 — zero price
    valid, reason = validate_price(0, 'gold-api.com')
    print(f'Test 2 (zero price):      {valid} — {reason}')

    # Test 3 — too low
    valid, reason = validate_price(500.0, 'gold-api.com')
    print(f'Test 3 (too low):         {valid} — {reason}')

    # Test 4 — too high
    valid, reason = validate_price(99000.0, 'gold-api.com')
    print(f'Test 4 (too high):        {valid} — {reason}')

    # Test 5 — massive spike (compared to DB)
    valid, reason = validate_price(25000.0, 'gold-api.com')
    print(f'Test 5 (spike):           {valid} — {reason}')

    # Test quality score
    print('\nTesting quality score...')
    test_data = {
        'data_source':  'gold-api.com',
        'usd_inr':      95.35,
        'retail_price': 15704.0,
        'price_24k':    13918.61
    }
    label, score, details, sources = get_data_quality(test_data)
    print(f'Quality: {label} ({score}/100)')
    print(f'Sources live: {sources}/3')
    for d in details:
        print(f'  → {d}')