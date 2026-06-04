import sys
import os

def _project_root() -> str:
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

sys.path.append(_project_root())

import datetime
from database.db_manager import get_price_history, get_setting
from core.analytics import get_buy_label


# ─── CHECK IF TODAY IS SUNDAY ─────────────────────────────────────────────────
def is_sunday():
    return datetime.datetime.now().weekday() == 6  # 0=Mon, 6=Sun


# ─── CHECK IF SUMMARY ALREADY SENT THIS WEEK ─────────────────────────────────
def already_sent_this_week():
    try:
        from database.db_manager import get_setting
        last_sent = get_setting('weekly_summary_last_sent')
        if not last_sent:
            return False

        last_date  = datetime.date.fromisoformat(last_sent)
        today      = datetime.date.today()

        # Same week = within last 7 days
        return (today - last_date).days < 7
    except Exception:
        return False


def mark_summary_sent():
    from database.db_manager import update_setting
    update_setting(
        'weekly_summary_last_sent',
        datetime.date.today().isoformat()
    )


# ─── BUILD SUMMARY DATA ───────────────────────────────────────────────────────
def build_weekly_summary():
    history = get_price_history(days=7)

    if len(history) < 2:
        return None

    prices     = [r['price_24k'] for r in history if r['price_24k']]
    timestamps = [r['timestamp'] for r in history if r['price_24k']]

    if not prices:
        return None

    current_price = prices[-1]
    week_start    = prices[0]
    week_high     = max(prices)
    week_low      = min(prices)
    week_change   = ((current_price - week_start) / week_start) * 100

    # Find best buy window (lowest price of the week)
    min_idx       = prices.index(week_low)
    best_buy_time = timestamps[min_idx][:16] if min_idx < len(timestamps) else 'N/A'

    # Current buy signal
    latest_score  = history[-1].get('buy_score') or 49
    buy_label     = get_buy_label(latest_score)

    return {
        'current_price': current_price,
        'week_start':    week_start,
        'week_change':   round(week_change, 2),
        'week_high':     week_high,
        'week_low':      week_low,
        'best_buy_time': best_buy_time,
        'buy_label':     buy_label,
        'buy_score':     latest_score,
        'data_points':   len(prices),
    }


# ─── FORMAT NOTIFICATION ─────────────────────────────────────────────────────
def format_summary_notification(summary):
    direction = '↑' if summary['week_change'] >= 0 else '↓'
    change    = abs(summary['week_change'])

    title = f"📊 GoldTracker Weekly Summary"
    message = (
        f"Gold this week: {direction} {change:.1f}%\n"
        f"High: ₹{summary['week_high']:,.0f}  "
        f"Low: ₹{summary['week_low']:,.0f}\n"
        f"Now: ₹{summary['current_price']:,.0f}/gram  "
        f"Signal: {summary['buy_label']}"
    )

    return title, message


# ─── MAIN: SEND SUMMARY IF DUE ───────────────────────────────────────────────
def send_weekly_summary_if_due(force=False):
    """
    Call this from scheduler every cycle.
    Only fires on Sundays and only once per week.
    force=True sends regardless (for testing).
    """
    if not force:
        # Check if sound toggle is enabled
        if get_setting('weekly_summary_enabled') == 'off':
            return

        if not is_sunday():
            return

        if already_sent_this_week():
            return

    summary = build_weekly_summary()

    if not summary:
        print('[WeeklySummary] Not enough data yet')
        return

    title, message = format_summary_notification(summary)

    # Fire notification
    try:
        from plyer import notification
        import os
        icon_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            'assets', 'icon.ico'
        )
        notification.notify(
            title    = title,
            message  = message,
            app_name = 'GoldTracker',
            app_icon = icon_path if os.path.exists(icon_path) else None,
            timeout  = 15,
        )
        print(f'[WeeklySummary] Sent: {message}')
    except Exception as e:
        print(f'[WeeklySummary] Notification error: {e}')

    mark_summary_sent()


# ─── Quick test ───────────────────────────────────────────────────────────────
if __name__ == '__main__':
    print('Testing weekly summary...\n')

    summary = build_weekly_summary()

    if summary:
        print('Summary data:')
        for k, v in summary.items():
            print(f'  {k}: {v}')

        title, message = format_summary_notification(summary)
        print(f'\nNotification title: {title}')
        print(f'Notification body:\n{message}')

        print('\nSending test notification (force=True)...')
        send_weekly_summary_if_due(force=True)
        print('Done.')
    else:
        print('Not enough data yet — need at least 2 price readings')