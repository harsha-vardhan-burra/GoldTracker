import requests
from bs4 import BeautifulSoup
import json
import os
import sys

# Add project root to path so we can import db_manager
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from database.db_manager import get_setting

# Load API key from settings.json
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SETTINGS_PATH = os.path.join(BASE_DIR, 'config', 'settings.json')

def load_api_key():
    try:
        with open(SETTINGS_PATH, 'r') as f:
            data = json.load(f)
            return data.get('goldapi_key', '')
    except FileNotFoundError:
        print('WARNING: config/settings.json not found')
        return ''


# ─── HEADERS (needed for scraping) ───────────────────────────────────────────
SCRAPE_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
    'Accept-Language': 'en-IN,en;q=0.9',
    'Referer': 'https://www.google.com',
}


# ─── FETCH 1: Gold spot price in USD ─────────────────────────────────────────
def fetch_spot_price_usd():
    # ── Primary: gold-api.com (no key, no limits) ──
    try:
        r = requests.get('https://api.gold-api.com/price/XAU', timeout=10)

        if r.status_code == 200:
            data       = r.json()
            price_oz   = data.get('price', 0)
            price_gram = round(price_oz / 31.1035, 4)
            print(f'[GoldAPI] Spot: ${price_oz}/oz → ${price_gram}/gram')
            return price_oz, price_gram, 'gold-api.com'
        else:
            print(f'[gold-api.com] Failed: {r.status_code} — trying fallback')

    except Exception as e:
        print(f'[gold-api.com] Error: {e} — trying fallback')

    # ── Fallback: GoldAPI.io (100 req/month, use sparingly) ──
    try:
        api_key = load_api_key()
        if api_key:
            url     = 'https://www.goldapi.io/api/XAU/USD'
            headers = {'x-access-token': api_key}
            r       = requests.get(url, headers=headers, timeout=10)

            if r.status_code == 200:
                data       = r.json()
                price_oz   = data.get('price', 0)
                price_gram = round(price_oz / 31.1035, 4)
                print(f'[GoldAPI.io Fallback] Spot: ${price_oz}/oz → ${price_gram}/gram')
                return price_oz, price_gram, 'goldapi.io'
            else:
                print(f'[GoldAPI.io] Failed: {r.status_code}')

    except Exception as e:
        print(f'[GoldAPI.io] Error: {e}')

    return None, None, 'unavailable'

# ─── FETCH 2: USD to INR exchange rate ───────────────────────────────────────
def fetch_usd_inr():
    try:
        url = 'https://api.frankfurter.dev/v2/rate/USD/INR'
        r = requests.get(url, timeout=10)

        if r.status_code == 200:
            data = r.json()
            rate = data.get('rate', 0)
            print(f'[Frankfurter] USD/INR: {rate}')
            return rate
        else:
            print(f'[Frankfurter] Failed: {r.status_code}')
            return None

    except Exception as e:
        print(f'[Frankfurter] Error: {e}')
        return None


# ─── FETCH 3: Retail price from GoodReturns (Vijayawada) ─────────────────────
def fetch_retail_price():
    try:
        city = get_setting('city') or 'vijayawada'
        url = f'https://www.goodreturns.in/gold-rates/{city}.html'
        r = requests.get(url, headers=SCRAPE_HEADERS, timeout=10)

        if r.status_code != 200:
            print(f'[GoodReturns] Failed: {r.status_code}')
            return None

        soup = BeautifulSoup(r.text, 'lxml')

        # Find the main gold rate table
        table_divs = soup.find_all('div', {'class': 'gr-table-wrap'})
        if not table_divs:
            print('[GoodReturns] Table not found — page structure may have changed')
            return None

        # Table 1 = today's rates (24K, 22K, 18K)
        rows = table_divs[0].find('table').find_all('tr')

        retail_24k = None
        retail_22k = None

        for row in rows[1:]:  # skip header row
            cols = row.find_all('td')
            if len(cols) >= 3:
                gram_label = cols[0].text.strip()

                # Only grab the 1 gram row
                if gram_label.strip() == '1':
                    try:
                        # Price cell contains "₹15,606\n(-223)"
                        # We only want the number before the newline
                        raw_24k = cols[1].text.strip().split('\n')[0]
                        raw_22k = cols[2].text.strip().split('\n')[0]

                        # Remove ₹ and commas
                        clean_24k = raw_24k.replace('₹', '').replace(',', '').strip()
                        clean_22k = raw_22k.replace('₹', '').replace(',', '').strip()

                        retail_24k = float(clean_24k)
                        retail_22k = float(clean_22k)
                        break
                    except ValueError as e:
                        print(f'[GoodReturns] Parse error: {e}')
                        continue
        
        if retail_24k:
            print(f'[GoodReturns] Retail 24K ({city}): ₹{retail_24k}/gram')
            print(f'[GoodReturns] Retail 22K ({city}): ₹{retail_22k}/gram')
        else:
            print('[GoodReturns] Could not parse retail price')

        return retail_24k

    except Exception as e:
        print(f'[GoodReturns] Error: {e}')
        return None


# ─── CALCULATE: Convert everything to INR ────────────────────────────────────
def calculate_inr_prices(spot_usd_per_gram, usd_inr):
    if not spot_usd_per_gram or not usd_inr:
        return None, None

    price_24k = round(spot_usd_per_gram * usd_inr, 2)
    price_22k = round(price_24k * (22 / 24), 2)

    print(f'[Calc] 24K: ₹{price_24k}/gram | 22K: ₹{price_22k}/gram')
    return price_24k, price_22k


# ─── MAIN: Fetch everything and return unified data object ───────────────────
def fetch_all():
    print('\n' + '─'*50)
    print('Fetching gold data...')
    print('─'*50)

    spot_usd_oz, spot_usd_gram, spot_source = fetch_spot_price_usd()
    usd_inr                    = fetch_usd_inr()
    retail_price               = fetch_retail_price()
    price_24k, price_22k       = calculate_inr_prices(spot_usd_gram, usd_inr)

     # ── Fallback: if GoldAPI fails, use retail as primary ──
    if not price_24k and retail_price:
        print('[DataEngine] GoldAPI unavailable — using retail as primary price')
        price_24k = retail_price
        price_22k = round(retail_price * (22/24), 2)

    result = {
        'spot_usd':     round(spot_usd_oz, 2)   if spot_usd_oz   else None,
        'usd_inr':      round(usd_inr, 4)        if usd_inr       else None,
        'price_24k':    price_24k,
        'price_22k':    price_22k,
        'retail_price': retail_price,  # 24K retail from GoodReturns

        # These get filled by analytics.py later
        'ma7':          None,
        'ma30':         None,
        'momentum':     None,
        'volatility':   None,
        'buy_score':    None,
        'sell_score':   None,
        'explanation':  None,
        'data_source':  spot_source
    }

    print('─'*50)
    print('Fetch complete.')
    print(f"  Spot (USD/oz) : ${result['spot_usd']}")
    print(f"  USD/INR       : {result['usd_inr']}")
    print(f"  24K (calc)    : ₹{result['price_24k']}/gram")
    print(f"  22K (calc)    : ₹{result['price_22k']}/gram")
    print(f"  Retail price  : ₹{result['retail_price']}/gram")
    print('─'*50 + '\n')

    return result


# ─── Quick test ───────────────────────────────────────────────────────────────
if __name__ == '__main__':
    data = fetch_all()