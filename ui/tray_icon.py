import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import threading
import pystray
from PIL import Image, ImageDraw, ImageFont
from database.db_manager import get_latest_price, get_setting
from utils.startup_manager import is_startup_enabled, sync_startup_setting


# ─── CREATE TRAY ICON IMAGE ──────────────────────────────────────────────────
def create_tray_image():
    """
    Creates the tray icon dynamically using Pillow.
    Falls back to a simple gold circle if icon.ico doesn't exist.
    """
    icon_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        'assets', 'icon.ico'
    )

    if os.path.exists(icon_path):
        try:
            return Image.open(icon_path).resize((64, 64))
        except Exception:
            pass

    # Generate icon programmatically
    img  = Image.new('RGBA', (64, 64), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.ellipse([2, 2, 62, 62], fill='#FFD700', outline='#B8860B', width=3)
    draw.text((20, 16), 'Au', fill='#000000')
    return img


# ─── GET TOOLTIP TEXT ────────────────────────────────────────────────────────
def get_tooltip_text():
    try:
        latest = get_latest_price()
        if latest and latest.get('price_24k'):
            price = latest['price_24k']
            score = latest.get('buy_score') or 49
            from core.analytics import get_buy_label
            label = get_buy_label(score)
            return f"GoldTracker | 24K: ₹{price:,.0f}/g | {label}"
        return "GoldTracker | Fetching..."
    except Exception:
        return "GoldTracker"


# ─── TRAY ICON CLASS ─────────────────────────────────────────────────────────
class GoldTrayIcon:
    def __init__(self, scheduler=None):
        self.scheduler      = scheduler
        self.icon           = None
        self.dashboard_open = False
        self.popup_open     = False
        self._build_icon()

    def _build_icon(self):
        menu = pystray.Menu(
            pystray.MenuItem(
                '📊 Open Dashboard',
                self._open_dashboard,
                default=True        # double-click action
            ),
            pystray.MenuItem(
                '⚡ Show Popup',
                self._show_popup
            ),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(
                '↻ Refresh Now',
                self._refresh_now
            ),
            pystray.MenuItem(
                '🔔 Launch on Startup',
                self._toggle_startup,
                checked=lambda item: is_startup_enabled()
            ),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(
                '✕ Quit GoldTracker',
                self._quit
            ),
        )

        self.icon = pystray.Icon(
            name    = 'GoldTracker',
            icon    = create_tray_image(),
            title   = get_tooltip_text(),
            menu    = menu
        )

    # ─── MENU ACTIONS ────────────────────────────────────────────────────────
    def _open_dashboard(self, icon=None, item=None):
        if self.dashboard_open:
            return

        def launch():
            self.dashboard_open = True
            try:
                from ui.dashboard import Dashboard
                import customtkinter as ctk
                app = Dashboard()

                def on_close():
                    self.dashboard_open = False
                    app.on_closing()

                app.protocol('WM_DELETE_WINDOW', on_close)
                app.mainloop()
            except Exception as e:
                print(f'[Tray] Dashboard error: {e}')
                self.dashboard_open = False

        t = threading.Thread(target=launch, daemon=True)
        t.start()

    def _show_popup(self, icon=None, item=None):
        if self.popup_open:
            return

        def launch():
            self.popup_open = True
            try:
                from ui.startup_popup import StartupPopup
                app = StartupPopup()

                def on_close():
                    self.popup_open = False
                    app.on_closing()

                app.protocol('WM_DELETE_WINDOW', on_close)
                app.mainloop()
            except Exception as e:
                print(f'[Tray] Popup error: {e}')
                self.popup_open = False

        t = threading.Thread(target=launch, daemon=True)
        t.start()

    def _refresh_now(self, icon=None, item=None):
        if self.scheduler:
            def do_refresh():
                result = self.scheduler.run_now()
                if result:
                    self._update_tooltip()
            t = threading.Thread(target=do_refresh, daemon=True)
            t.start()
        else:
            print('[Tray] No scheduler attached')

    def _toggle_startup(self, icon=None, item=None):
        current = is_startup_enabled()
        sync_startup_setting(not current)

    def _quit(self, icon=None, item=None):
        print('[Tray] Quitting GoldTracker...')
        if self.scheduler:
            self.scheduler.stop()
        self.icon.stop()

    # ─── TOOLTIP UPDATE ──────────────────────────────────────────────────────
    def _update_tooltip(self):
        if self.icon:
            self.icon.title = get_tooltip_text()

    def update_from_data(self, data):
        """Called by scheduler on_update callback."""
        try:
            price = data.get('price_24k')
            label = data.get('buy_label', '')
            if price:
                self.icon.title = f"GoldTracker | 24K: ₹{price:,.0f}/g | {label}"
        except Exception:
            pass

    # ─── START / STOP ────────────────────────────────────────────────────────
    def start(self):
        """Runs tray icon — MUST be called from main thread."""
        print('[Tray] Starting system tray icon...')
        self.icon.run()

    def start_in_thread(self):
        """Runs tray icon in background thread."""
        t = threading.Thread(target=self.start, daemon=True)
        t.start()

    def stop(self):
        if self.icon:
            self.icon.stop()


# ─── Quick test ───────────────────────────────────────────────────────────────
if __name__ == '__main__':
    from core.scheduler import GoldScheduler

    print('Starting GoldTracker in tray mode...')
    print('Look for the gold icon in your system tray (bottom right)')
    print('Right-click it to see the menu')

    # Start scheduler
    scheduler = GoldScheduler()
    scheduler.start()

    # Start tray icon — attach scheduler so Refresh works
    tray = GoldTrayIcon(scheduler=scheduler)

    # Wire scheduler updates to tray tooltip
    scheduler.on_update = tray.update_from_data

    # This blocks until user clicks Quit
    tray.start()

    print('GoldTracker stopped.')