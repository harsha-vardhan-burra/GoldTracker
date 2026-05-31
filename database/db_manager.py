import sqlite3
import os

# Always store the database inside the project folder
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(BASE_DIR, 'database', 'goldtracker.db')


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
    conn.commit()
    conn.close()
    print('Database initialized successfully at:', DB_PATH)

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

def insert_price(data: dict):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO price_history (
            spot_usd, usd_inr, price_24k, price_22k, retail_price,
            ma7, ma30, momentum, volatility,
            buy_score, sell_score, explanation,
            data_source
        ) VALUES (
            :spot_usd, :usd_inr, :price_24k, :price_22k, :retail_price,
            :ma7, :ma30, :momentum, :volatility,
            :buy_score, :sell_score, :explanation,
            :data_source
        )
    ''', data)
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