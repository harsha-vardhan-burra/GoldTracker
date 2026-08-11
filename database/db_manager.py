import sqlite3
import os
import datetime
import sys

def _get_base_dir() -> str:
    """
    Returns the correct base directory whether running as:
    - Python script (development)
    - PyInstaller .exe (production)
    """
    if getattr(sys, 'frozen', False):
        # Running as compiled .exe
        # sys.executable = path to the .exe file
        return os.path.dirname(sys.executable)
    else:
        # Running as Python script
        return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

BASE_DIR = _get_base_dir()
DB_PATH  = os.path.join(BASE_DIR, 'database', 'goldtracker.db')


def get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row  # lets you access columns by name
    return conn


def initialize_database():
    conn = get_connection()
    cursor = conn.cursor()

    # Main price history table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS price_history (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp       DATETIME DEFAULT CURRENT_TIMESTAMP,

            -- Raw fetched values
            spot_usd        REAL,
            usd_inr         REAL,
            price_24k       REAL,
            price_22k       REAL,
            retail_price    REAL,

            -- Computed analytics
            ma7             REAL,
            ma30            REAL,
            momentum        REAL,
            volatility      REAL,

            -- Scores and explanation
            buy_score       INTEGER,
            sell_score      INTEGER,
            explanation     TEXT,

            -- Data source tracking
            data_source     TEXT DEFAULT 'unknown'
        )
    ''')

    # Alerts table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS alerts (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            type            TEXT NOT NULL,
            target_price    REAL NOT NULL,
            created_at      DATETIME DEFAULT CURRENT_TIMESTAMP,
            triggered_at    DATETIME,
            status          TEXT DEFAULT 'active'
        )
    ''')

    # Settings table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS settings (
            key             TEXT PRIMARY KEY,
            value           TEXT
        )
    ''')

    # Insert default settings if not already present
    defaults = [
        ('city',                    'vijayawada'),
        ('karat',                   '24'),
        ('polling_interval',        '5'),
        ('startup_enabled',         'true'),
        ('theme',                   'dark'),
        ('target_buy_price',        'null'),
        ('target_sell_price',       'null'),
        ('sound_enabled',           'on'),
        ('spike_alerts_enabled',    'on'),
        ('weekly_summary_enabled',  'on'),
        ('weekly_summary_last_sent', ''),
        ('news_enabled',      'on'),
        ('news_last_fetched', ''),
        ('news_query_index', '0'),
    ]
    cursor.executemany('''
        INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)
    ''', defaults)
    # Portfolio table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS portfolio (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            purchase_date   DATE NOT NULL,
            karat           TEXT NOT NULL,
            grams           REAL NOT NULL,
            price_per_gram  REAL NOT NULL,
            total_invested  REAL NOT NULL,
            notes           TEXT,
            created_at      DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    # Anomaly log table
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
    # Analytics history table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS analytics_history (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp       DATETIME DEFAULT CURRENT_TIMESTAMP,
            signal          TEXT,
            score           REAL,
            confidence      REAL,
            reasoning       TEXT,
            adx             REAL,
            atr             REAL,
            volatility      REAL,
            retail_premium  REAL
        )
    ''')
    conn.commit()
    conn.close()
    print('Database initialized successfully at:', DB_PATH)

    # Run column migrations for tables that predate these fields.
    # Both are idempotent (check PRAGMA table_info before altering),
    # so it's safe to call them unconditionally on every startup.
    migrate_add_source_column()
    migrate_add_analytics_columns()
    migrate_add_analytics_history_table()

def migrate_add_analytics_history_table():
    """
    Ensures analytics_history table exists and contains all required columns.
    Idempotent and safe to run on existing databases.
    """
    conn   = get_connection()
    cursor = conn.cursor()

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS analytics_history (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp       DATETIME DEFAULT CURRENT_TIMESTAMP,
            signal          TEXT,
            score           REAL,
            confidence      REAL,
            reasoning       TEXT,
            adx             REAL,
            atr             REAL,
            volatility      REAL,
            retail_premium  REAL
        )
    ''')
    conn.commit()

    cursor.execute("PRAGMA table_info(analytics_history)")
    columns = [row['name'] for row in cursor.fetchall()]

    new_cols = [
        ('signal', 'TEXT'),
        ('score', 'REAL'),
        ('confidence', 'REAL'),
        ('reasoning', 'TEXT'),
        ('adx', 'REAL'),
        ('atr', 'REAL'),
        ('volatility', 'REAL'),
        ('retail_premium', 'REAL')
    ]

    for col_name, col_type in new_cols:
        if col_name not in columns:
            cursor.execute(f'ALTER TABLE analytics_history ADD COLUMN {col_name} {col_type}')
            print(f'[Migration] Added {col_name} column to analytics_history')

    conn.commit()
    conn.close()


def save_analytics(analytics_data):
    """
    Persists AnalyticsResult or analytics dictionary into analytics_history table.
    """
    if hasattr(analytics_data, 'to_dict'):
        d = analytics_data.to_dict()
    elif isinstance(analytics_data, dict):
        d = analytics_data
    else:
        d = {}

    conn   = get_connection()
    cursor = conn.cursor()

    signal = d.get('buy_label') or d.get('signal') or 'NEUTRAL'
    score  = d.get('buy_score') if d.get('buy_score') is not None else d.get('score')
    confidence = d.get('confidence')
    reasoning  = d.get('explanation') or d.get('reasoning')
    adx        = d.get('adx_value') or d.get('trend_adx') or d.get('adx')
    atr        = d.get('atr_value') or d.get('atr') or d.get('volatility')
    volatility = d.get('volatility')

    premium_val = d.get('retail_premium')
    if premium_val is None and isinstance(d.get('premium_stats'), dict):
        premium_val = d['premium_stats'].get('current_premium')

    cursor.execute('''
        INSERT INTO analytics_history (
            timestamp, signal, score, confidence, reasoning,
            adx, atr, volatility, retail_premium
        ) VALUES (
            CURRENT_TIMESTAMP, ?, ?, ?, ?, ?, ?, ?, ?
        )
    ''', (signal, score, confidence, reasoning, adx, atr, volatility, premium_val))

    conn.commit()
    conn.close()

insert_analytics = save_analytics

def migrate_add_source_column():
    """
    Adds data_source column to price_history if it doesn't exist.
    Safe to run multiple times — checks before adding.
    """
    conn   = get_connection()
    cursor = conn.cursor()

    # Check if column already exists
    cursor.execute("PRAGMA table_info(price_history)")
    columns = [row['name'] for row in cursor.fetchall()]

    if 'data_source' not in columns:
        cursor.execute('''
            ALTER TABLE price_history 
            ADD COLUMN data_source TEXT DEFAULT 'unknown'
        ''')
        conn.commit()
        print('[Migration] Added data_source column to price_history')
    else:
        print('[Migration] data_source column already exists — skipping')

    conn.close()


def migrate_add_analytics_columns():
    """
    Adds confidence, trend, and support/resistance columns to price_history
    if they don't already exist. Safe to run multiple times.

    These were previously computed by analytics.run_analytics() but never
    persisted past the in-memory scheduler cycle — every restart lost them.
    """
    conn   = get_connection()
    cursor = conn.cursor()

    cursor.execute("PRAGMA table_info(price_history)")
    columns = [row['name'] for row in cursor.fetchall()]

    new_columns = [
        ('confidence',       'INTEGER'),
        ('confidence_label', 'TEXT'),
        ('trend_adx',        'REAL'),
        ('support',          'REAL'),
        ('resistance',       'REAL'),
        ('data_quality',       'TEXT'),
        ('data_quality_score', 'INTEGER'),
    ]

    added_any = False
    for col_name, col_type in new_columns:
        if col_name not in columns:
            cursor.execute(f'ALTER TABLE price_history ADD COLUMN {col_name} {col_type}')
            print(f'[Migration] Added {col_name} column to price_history')
            added_any = True

    if added_any:
        conn.commit()
    else:
        print('[Migration] Analytics columns already exist — skipping')

    conn.close()

def insert_price(data: dict):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO price_history (
            spot_usd, usd_inr, price_24k, price_22k, retail_price,
            ma7, ma30, momentum, volatility,
            buy_score, sell_score, explanation,
            data_source,
            confidence, confidence_label, trend_adx, support, resistance,
            data_quality, data_quality_score
        ) VALUES (
            :spot_usd, :usd_inr, :price_24k, :price_22k, :retail_price,
            :ma7, :ma30, :momentum, :volatility,
            :buy_score, :sell_score, :explanation,
            :data_source,
            :confidence, :confidence_label, :trend_adx, :support, :resistance,
            :data_quality, :data_quality_score
        )
    ''', {
        **{
            'data_source': 'unknown',
            'confidence': None, 'confidence_label': None, 'trend_adx': None,
            'support': None, 'resistance': None,
            'data_quality': None, 'data_quality_score': None,
        },
        **data,
    })
    conn.commit()
    conn.close()


def get_latest_price():
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute('''
        SELECT * FROM price_history
        ORDER BY timestamp DESC
        LIMIT 1
    ''')
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None


def get_price_history(days=30):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute('''
        SELECT * FROM price_history
        WHERE timestamp >= datetime('now', ?)
        ORDER BY timestamp ASC
    ''', (f'-{days} days',))
    rows = cursor.fetchall()
    conn.close()
    return [dict(row) for row in rows]


_WEEKDAY_NAMES = {
    '0': 'Sunday',    '1': 'Monday',  '2': 'Tuesday', '3': 'Wednesday',
    '4': 'Thursday',  '5': 'Friday',  '6': 'Saturday',
}


def get_historical_insights():
    """
    Answers the "useful even when you're not buying" questions:
    lowest price this month, highest this year, average buying
    opportunity, best day of week to buy, average monthly volatility.

    Returns a dict; any stat with too little data to be meaningful
    comes back as None so the UI can show "building..." instead of
    a misleading number.
    """
    conn   = get_connection()
    cursor = conn.cursor()

    def _real_rows_filter(days):
        return (
            "price_24k IS NOT NULL AND price_24k > 0 "
            "AND data_source != 'gap_marker' "
            f"AND timestamp >= datetime('now', '-{days} days')"
        )

    result = {
        'lowest_price_30d':     None,
        'lowest_price_30d_at':  None,
        'highest_price_365d':   None,
        'highest_price_365d_at':None,
        'avg_buy_score_30d':    None,
        'best_buy_weekday':     None,
        'best_buy_weekday_avg': None,
        'avg_volatility_30d':   None,
    }

    # Lowest price — last 30 days
    cursor.execute(f'''
        SELECT price_24k, timestamp FROM price_history
        WHERE {_real_rows_filter(30)}
        ORDER BY price_24k ASC LIMIT 1
    ''')
    row = cursor.fetchone()
    if row:
        result['lowest_price_30d']    = row['price_24k']
        result['lowest_price_30d_at'] = row['timestamp']

    # Highest price — last 365 days
    cursor.execute(f'''
        SELECT price_24k, timestamp FROM price_history
        WHERE {_real_rows_filter(365)}
        ORDER BY price_24k DESC LIMIT 1
    ''')
    row = cursor.fetchone()
    if row:
        result['highest_price_365d']    = row['price_24k']
        result['highest_price_365d_at'] = row['timestamp']

    # Average buying opportunity — mean buy_score, last 30 days
    cursor.execute('''
        SELECT AVG(buy_score) as avg_score FROM price_history
        WHERE buy_score IS NOT NULL
        AND data_source != 'gap_marker'
        AND timestamp >= datetime('now', '-30 days')
    ''')
    row = cursor.fetchone()
    if row and row['avg_score'] is not None:
        result['avg_buy_score_30d'] = round(row['avg_score'], 1)

    # Best day of week to buy — highest average buy_score by weekday,
    # using all history so the sample size is meaningful.
    cursor.execute('''
        SELECT strftime('%w', timestamp) as dow,
               AVG(buy_score) as avg_score,
               COUNT(*) as n
        FROM price_history
        WHERE buy_score IS NOT NULL
        AND data_source != 'gap_marker'
        GROUP BY dow
        HAVING n >= 3
        ORDER BY avg_score DESC
        LIMIT 1
    ''')
    row = cursor.fetchone()
    if row:
        result['best_buy_weekday']     = _WEEKDAY_NAMES.get(row['dow'])
        result['best_buy_weekday_avg'] = round(row['avg_score'], 1)

    # Average volatility — last 30 days
    cursor.execute('''
        SELECT AVG(volatility) as avg_vol FROM price_history
        WHERE volatility IS NOT NULL
        AND data_source != 'gap_marker'
        AND timestamp >= datetime('now', '-30 days')
    ''')
    row = cursor.fetchone()
    if row and row['avg_vol'] is not None:
        result['avg_volatility_30d'] = round(row['avg_vol'], 1)

    conn.close()
    return result


def insert_gap_marker(gap_minutes):
    """
    Inserts a special row marking that the app was offline.
    Charts use this to draw breaks instead of false lines.

    """
    conn   = get_connection()
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO price_history (
            spot_usd, usd_inr, price_24k, price_22k, retail_price,
            ma7, ma30, momentum, volatility,
            buy_score, sell_score, explanation,
            data_source
        ) VALUES (
            NULL, NULL, NULL, NULL, NULL,
            NULL, NULL, NULL, NULL,
            NULL, NULL, ?,
            'gap_marker'
        )
    ''', (f'App offline for {gap_minutes} minutes',))
    conn.commit()
    conn.close()
    print(f'[GapHandler] Marked gap: app was offline for {gap_minutes} minutes')


def get_last_reading_age_minutes():
    """
    Returns how many minutes ago the last real price was stored.
    Returns None if no readings exist.
    """
    conn   = get_connection()
    cursor = conn.cursor()
    cursor.execute('''
        SELECT timestamp FROM price_history
        WHERE data_source != 'gap_marker'
        ORDER BY timestamp DESC
        LIMIT 1
    ''')
    row = cursor.fetchone()
    conn.close()

    if not row:
        return None

    try:
        import datetime as dt
        last_time = dt.datetime.fromisoformat(str(row['timestamp']))
        diff      = dt.datetime.now() - last_time
        return diff.total_seconds() / 60
    except Exception:
        return None

def get_setting(key):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute('SELECT value FROM settings WHERE key = ?', (key,))
    row = cursor.fetchone()
    conn.close()
    return row['value'] if row else None


def update_setting(key, value):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute('''
        INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)
    ''', (key, str(value)))
    conn.commit()
    conn.close()


def get_active_alerts():
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute('''
        SELECT * FROM alerts WHERE status = 'active'
    ''')
    rows = cursor.fetchall()
    conn.close()
    return [dict(row) for row in rows]

def get_alert_history():
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute('''
        SELECT * FROM alerts 
        WHERE status = 'triggered'
        ORDER BY triggered_at DESC
    ''')
    rows = cursor.fetchall()
    conn.close()
    return [dict(row) for row in rows]

def get_all_alerts():
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute('''
        SELECT * FROM alerts 
        ORDER BY created_at DESC
    ''')
    rows = cursor.fetchall()
    conn.close()
    return [dict(row) for row in rows]

def cancel_alert(alert_id):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute('''
        UPDATE alerts SET status = 'cancelled'
        WHERE id = ?
    ''', (alert_id,))
    conn.commit()
    conn.close()

def add_alert(alert_type, target_price):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO alerts (type, target_price) VALUES (?, ?)
    ''', (alert_type, target_price))
    conn.commit()
    conn.close()


def trigger_alert(alert_id):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute('''
        UPDATE alerts
        SET status = 'triggered', triggered_at = CURRENT_TIMESTAMP
        WHERE id = ?
    ''', (alert_id,))
    conn.commit()
    conn.close()

def initialize_portfolio_table():
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS portfolio (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            purchase_date   DATE NOT NULL,
            karat           TEXT NOT NULL,
            grams           REAL NOT NULL,
            price_per_gram  REAL NOT NULL,
            total_invested  REAL NOT NULL,
            notes           TEXT,
            created_at      DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    conn.commit()
    conn.close()


def add_purchase(purchase_date, karat, grams, price_per_gram, notes=''):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO portfolio (
            purchase_date, karat, grams, 
            price_per_gram, total_invested, notes
        ) VALUES (?, ?, ?, ?, ?, ?)
    ''', (
        purchase_date, karat, grams,
        price_per_gram,
        round(grams * price_per_gram, 2),
        notes
    ))
    conn.commit()
    conn.close()


def get_portfolio():
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute('''
        SELECT * FROM portfolio 
        ORDER BY purchase_date DESC
    ''')
    rows = cursor.fetchall()
    conn.close()
    return [dict(row) for row in rows]


def delete_purchase(purchase_id):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute('DELETE FROM portfolio WHERE id = ?', (purchase_id,))
    conn.commit()
    conn.close()


def get_portfolio_summary():
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute('''
        SELECT 
            SUM(grams)          as total_grams,
            SUM(total_invested) as total_invested,
            AVG(price_per_gram) as avg_buy_price
        FROM portfolio
    ''')
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None

# --- Quick test ---
if __name__ == '__main__':
    initialize_database()
    
    # Test insert
    test_data = {
        'spot_usd':     3350.0,
        'usd_inr':      83.67,
        'price_24k':    9020.5,
        'price_22k':    8268.8,
        'retail_price': 9150.0,
        'ma7':          None,
        'ma30':         None,
        'momentum':     None,
        'volatility':   None,
        'buy_score':    None,
        'sell_score':   None,
        'explanation':  None,
    }
    insert_price(test_data)
    print('Test row inserted.')

    latest = get_latest_price()
    print('Latest price fetched:', latest)

    setting = get_setting('city')
    print('City setting:', setting)

    print('All database functions working correctly.')