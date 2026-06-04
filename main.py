import sys
import os
import threading

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.append(BASE_DIR)

from database.db_manager   import initialize_database, get_setting
from utils.startup_manager import sync_startup_setting
from core.scheduler        import GoldScheduler
from ui.tray_icon          import GoldTrayIcon

def ensure_directory_structure() -> None:
    """
    Creates required folders and default config on first run.
    Works both in development and packaged .exe context.
    """
    import shutil

    base = _get_base_dir()

    # Folders that must exist
    for folder in ('config', 'database', 'assets'):
        path = os.path.join(base, folder)
        os.makedirs(path, exist_ok=True)

    # Create settings.json from example if missing
    settings_path = os.path.join(base, 'config', 'settings.json')
    example_path  = os.path.join(base, 'config', 'settings.example.json')

    if not os.path.exists(settings_path):
        if os.path.exists(example_path):
            shutil.copy(example_path, settings_path)
            print('[Setup] Created config/settings.json from example')
        else:
            # Create a minimal default settings.json from scratch
            import json
            defaults = {
                "goldapi_key":              "",
                "gnews_api_key":            "",
                "city":                     "hyderabad",
                "karat":                    "24",
                "polling_interval_minutes": 5,
                "startup_enabled":          True,
                "theme":                    "dark",
                "target_buy_price":         None,
                "target_sell_price":        None
            }
            with open(settings_path, 'w') as f:
                json.dump(defaults, f, indent=4)
            print('[Setup] Created default config/settings.json')

def _get_base_dir() -> str:
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))

def main():
    print('='*50)
    print('  GoldTracker Starting...')
    print('='*50)

    # Step 0 — Ensure directory structure exists
    ensure_directory_structure()

    # Step 1 — Initialize database
    print('[Main] Initializing database...')
    initialize_database()

    # Step 2 — Sync startup registry
    startup_enabled = get_setting('startup_enabled')
    if startup_enabled == 'true':
        sync_startup_setting(True)

    # Step 3 — Start background scheduler
    print('[Main] Starting scheduler...')
    scheduler = GoldScheduler()
    scheduler.start()

    # Step 4 — Create tray icon and wire scheduler
    tray = GoldTrayIcon(scheduler=scheduler)
    scheduler.on_update = tray.update_from_data

    # Step 5 — Decide launch mode
    launch_mode = 'popup' if '--startup' in sys.argv else 'dashboard'
    print(f'[Main] Launch mode: {launch_mode}')

    # Step 6 — Show initial window in separate thread
    if launch_mode == 'popup':
        t = threading.Thread(target=_launch_popup, args=(scheduler,), daemon=True)
    else:
        t = threading.Thread(target=_launch_dashboard, daemon=True)

    t.start()

    # Step 7 — Run tray icon on main thread (required by pystray)
    print('[Main] GoldTracker running in system tray.')
    print('[Main] Right-click the tray icon to access all features.')
    tray.start()  # blocks here until user quits

    print('[Main] GoldTracker exited cleanly.')


def _launch_popup(scheduler):
    try:
        from ui.startup_popup import StartupPopup
        import customtkinter as ctk

        app = StartupPopup()

        ctk.CTkButton(
            app,
            text='Open Full Dashboard →',
            fg_color='transparent',
            hover_color='#2a2a2a',
            text_color='#AAAAAA',
            font=ctk.CTkFont(size=11),
            command=lambda: _switch_to_dashboard(app)
        ).pack(pady=(0, 8))

        app.protocol('WM_DELETE_WINDOW', app.on_closing)
        app.mainloop()
    except Exception as e:
        print(f'[Main] Popup error: {e}')


def _launch_dashboard():
    try:
        from ui.dashboard import Dashboard
        app = Dashboard()
        app.protocol('WM_DELETE_WINDOW', app.on_closing)
        app.mainloop()
    except Exception as e:
        print(f'[Main] Dashboard error: {e}')


def _switch_to_dashboard(popup):
    popup.on_closing()
    _launch_dashboard()


if __name__ == '__main__':
    main()