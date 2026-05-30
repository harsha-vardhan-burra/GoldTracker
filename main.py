import sys
import os
import threading

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.append(BASE_DIR)

from database.db_manager   import initialize_database, get_setting
from utils.startup_manager import sync_startup_setting
from core.scheduler        import GoldScheduler
from ui.tray_icon          import GoldTrayIcon


def main():
    print('='*50)
    print('  GoldTracker Starting...')
    print('='*50)

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
        app = StartupPopup()

        import customtkinter as ctk
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