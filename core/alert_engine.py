import sys
import os

def _project_root() -> str:
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

sys.path.append(_project_root())

from database.db_manager import get_active_alerts, trigger_alert
from plyer import notification
import threading
import datetime


# ─── NOTIFICATION SETTINGS ───────────────────────────────────────────────────
APP_NAME    = 'GoldTracker'
ICON_PATH   = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    'assets', 'icon.ico'
)


# ─── SOUND ALERT ─────────────────────────────────────────────────────────────
def play_alert_sound():
    try:
        # Check if sound is enabled in settings
        from database.db_manager import get_setting
        if get_setting('sound_enabled') == 'off':
            print('[AlertEngine] Sound disabled — skipping')
            return

        import winsound
        winsound.Beep(1000, 200)
        winsound.Beep(1200, 200)
        winsound.Beep(1000, 300)
    except Exception as e:
        print(f'[AlertEngine] Sound error: {e}')


# ─── FIRE NOTIFICATION ───────────────────────────────────────────────────────
def fire_notification(title, message):
    try:
        icon = ICON_PATH if os.path.exists(ICON_PATH) else None
        notification.notify(
            title       = title,
            message     = message,
            app_name    = APP_NAME,
            app_icon    = icon,
            timeout     = 10,        # notification stays for 10 seconds
        )
        print(f'[AlertEngine] Notification fired: {title} — {message}')
    except Exception as e:
        print(f'[AlertEngine] Notification error: {e}')


# ─── CHECK ALL ACTIVE ALERTS ─────────────────────────────────────────────────
def check_alerts(current_price_24k):
    """
    Called after every fetch cycle with the latest 24K price.
    Checks all active alerts and fires notifications for any that are hit.
    Returns list of triggered alert IDs.
    """
    if not current_price_24k:
        return []

    alerts        = get_active_alerts()
    triggered_ids = []

    for alert in alerts:
        alert_id     = alert['id']
        alert_type   = alert['type']
        target_price = alert['target_price']

        triggered = False
        title     = ''
        message   = ''

        if alert_type == 'buy' and current_price_24k <= target_price:
            triggered = True
            title     = '🟢 Buy Alert Triggered!'
            message   = (
                f"24K gold has dropped to ₹{current_price_24k:,.2f}/gram\n"
                f"Your target: ₹{target_price:,.0f}/gram"
            )

        elif alert_type == 'sell' and current_price_24k >= target_price:
            triggered = True
            title     = '🔴 Sell Alert Triggered!'
            message   = (
                f"24K gold has risen to ₹{current_price_24k:,.2f}/gram\n"
                f"Your target: ₹{target_price:,.0f}/gram"
            )

        if triggered:
            # Mark as triggered in DB first
            trigger_alert(alert_id)
            triggered_ids.append(alert_id)

            # Fire notification + sound in separate thread
            # so it doesn't block the main fetch cycle
            t = threading.Thread(
                target=_fire_alert_async,
                args=(title, message),
                daemon=True
            )
            t.start()

            print(
                f'[AlertEngine] TRIGGERED — {alert_type.upper()} alert '
                f'| Target: ₹{target_price:,.0f} '
                f'| Current: ₹{current_price_24k:,.2f}'
            )

    return triggered_ids


def _fire_alert_async(title, message):
    play_alert_sound()
    fire_notification(title, message)


# ─── PRICE CHANGE ALERT (automatic, no user setup needed) ────────────────────
def check_price_spike(current_price, previous_price, threshold_pct=5.0):
    # Check if spike alerts are enabled
    from database.db_manager import get_setting
    if get_setting('spike_alerts_enabled') == 'off':
        return

    if not current_price or not previous_price:
        return

    change_pct = ((current_price - previous_price) / previous_price) * 100

    if abs(change_pct) >= threshold_pct:
        direction = 'risen' if change_pct > 0 else 'fallen'
        title   = f"⚡ Gold Price Alert"
        message = (
            f"Gold has {direction} by {abs(change_pct):.1f}% "
            f"since last reading\n"
            f"Current: ₹{current_price:,.2f}/gram"
        )
        t = threading.Thread(
            target=_fire_alert_async,
            args=(title, message),
            daemon=True
        )
        t.start()
        print(f'[AlertEngine] Price spike detected: {change_pct:+.2f}%')


# ─── Quick test ───────────────────────────────────────────────────────────────
if __name__ == '__main__':
    from database.db_manager import add_alert, initialize_database

    initialize_database()

    print('Testing alert engine...\n')

    # Add a test alert that will definitely trigger
    # (setting target above current price for buy = won't trigger)
    # (setting target below current price for buy = will trigger)
    test_price = 13500.0

    print(f'Current simulated price: ₹{test_price:,.2f}/gram')

    # Add a buy alert above current price → should trigger
    add_alert('buy', 14000.0)
    print('Added buy alert at ₹14,000 (above current → should trigger)')

    # Add a sell alert below current price → should trigger
    add_alert('sell', 13000.0)
    print('Added sell alert at ₹13,000 (below current → should trigger)')

    # Add alerts that should NOT trigger
    add_alert('buy', 13000.0)
    print('Added buy alert at ₹13,000 (below current → should NOT trigger)')

    print('\nRunning alert check...')
    triggered = check_alerts(test_price)
    print(f'\nTriggered alert IDs: {triggered}')

    # Test price spike detection
    print('\nTesting price spike detection...')
    check_price_spike(test_price, test_price * 0.97)  # 3% drop → should fire
    check_price_spike(test_price, test_price * 0.99)  # 1% drop → should NOT fire

    import time
    time.sleep(3)  # wait for async notifications
    print('\nAlert engine test complete.')