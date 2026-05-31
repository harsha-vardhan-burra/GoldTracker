import sys
import os
import time
import threading
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    # When imported from main.py (root context)
    from core.alert_engine  import check_alerts, check_price_spike
    from core.weekly_summary import send_weekly_summary_if_due
    from core.news_engine   import get_news_context
    from core.data_engine   import fetch_all
    from core.analytics     import run_analytics
    from database.db_manager import insert_price, get_setting, get_price_history
except ImportError:
    # When run directly (core context)
    from alert_engine  import check_alerts, check_price_spike
    from weekly_summary import send_weekly_summary_if_due
    from news_engine   import get_news_context
    from data_engine   import fetch_all
    from analytics     import run_analytics
    sys.path.append(os.path.dirname(os.path.abspath(__file__)))
    from database.db_manager import insert_price, get_setting, get_price_history


# ─── SINGLE FETCH + STORE CYCLE ──────────────────────────────────────────────
def run_cycle():
    print('\n[Scheduler] Running fetch cycle...')

    try:
        # Step 1: Fetch all price data
        data = fetch_all()

        if not data or not data.get('price_24k'):
            print('[Scheduler] Fetch returned no usable data — skipping this cycle')
            return None

        # Step 2: Run analytics on current price
        analytics = run_analytics(data['price_24k'])

        # Step 3: Merge analytics into data
        data['ma7']         = analytics['ma7']
        data['ma30']        = analytics['ma30']
        data['momentum']    = analytics['momentum']
        data['volatility']  = analytics['volatility']
        data['buy_score']   = analytics['buy_score']
        data['sell_score']  = analytics['sell_score']
        data['explanation'] = analytics['explanation']

        # Step 4: Store in database
        insert_price(data)
        print(f"[Scheduler] Stored → 24K: ₹{data['price_24k']} | "
              f"Buy: {analytics['buy_score']}/100 ({analytics['buy_label']}) | "
              f"Sell: {analytics['sell_score']}/100 ({analytics['sell_label']})")
        
        # Step 5: Check alerts
        check_alerts(data['price_24k'])

        # Step 6: Check for sudden price spikes
        from database.db_manager import get_price_history
        history = get_price_history(days=1)
        if len(history) >= 2:
            prev_price = history[-2]['price_24k']
            check_price_spike(data['price_24k'], prev_price)

        # Step 7: Weekly summary (Sundays only)
        send_weekly_summary_if_due()

        # Step 8: Fetch news context (hourly)
        news = get_news_context()
        if news and news.get('reasoning'):
            # Append news reasoning to explanation
            current_explanation = data.get('explanation') or ''
            data['explanation'] = f"{current_explanation} · {news['reasoning']}" \
                                  if current_explanation else news['reasoning']

        # Return full result including labels for UI to use
        data['buy_label']  = analytics['buy_label']
        data['sell_label'] = analytics['sell_label']
        return data

    except Exception as e:
        print(f'[Scheduler] Error during cycle: {e}')
        return None


# ─── BACKGROUND POLLING LOOP ─────────────────────────────────────────────────
class GoldScheduler:
    def __init__(self, on_update=None):
        """
        on_update: optional callback function that receives
                   the latest data dict after every cycle.
                   UI will pass its refresh function here.
        """
        self.on_update       = on_update
        self.running         = False
        self.thread          = None
        self.interval_mins   = int(get_setting('polling_interval') or 5)
        self.interval_secs   = self.interval_mins * 60

    def _loop(self):
        while self.running:
            result = run_cycle()

            # Notify UI if callback is registered
            if result and self.on_update:
                try:
                    self.on_update(result)
                except Exception as e:
                    print(f'[Scheduler] UI callback error: {e}')

            # Wait for next interval, but check every second
            # so we can stop quickly when app closes
            for _ in range(self.interval_secs):
                if not self.running:
                    break
                time.sleep(1)

    def start(self):
        if self.running:
            print('[Scheduler] Already running')
            return
        self.running = True
        self.thread  = threading.Thread(target=self._loop, daemon=True)
        self.thread.start()
        print(f'[Scheduler] Started — polling every {self.interval_mins} minute(s)')

    def stop(self):
        self.running = False
        print('[Scheduler] Stopped')

    def run_now(self):
        """Force an immediate fetch outside the normal interval."""
        return run_cycle()


# ─── Quick test ───────────────────────────────────────────────────────────────
if __name__ == '__main__':
    print('Testing single cycle...')
    result = run_cycle()

    if result:
        print('\n' + '='*50)
        print('CYCLE RESULT')
        print('='*50)
        print(f"24K Price  : ₹{result['price_24k']}/gram")
        print(f"22K Price  : ₹{result['price_22k']}/gram")
        print(f"Retail     : ₹{result['retail_price']}/gram")
        print(f"Buy Signal : {result['buy_label']} ({result['buy_score']}/100)")
        print(f"Sell Signal: {result['sell_label']} ({result['sell_score']}/100)")
        print(f"Explanation: {result['explanation']}")
        print('='*50)

        print('\nTesting background scheduler for 15 seconds...')
        print('(You should see a second fetch happen automatically)\n')

        def on_update(data):
            print(f"[UI Callback] Price updated: ₹{data['price_24k']}/gram")

        scheduler = GoldScheduler(on_update=on_update)
        scheduler.interval_secs = 10   # 10 seconds for testing only
        scheduler.start()

        time.sleep(15)
        scheduler.stop()
        print('\nScheduler test complete.')