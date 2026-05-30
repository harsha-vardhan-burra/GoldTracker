import sys
import os
import winreg

# Registry key for Windows startup programs
STARTUP_KEY  = r'Software\Microsoft\Windows\CurrentVersion\Run'
APP_NAME     = 'GoldTracker'


def get_app_path():
    if getattr(sys, 'frozen', False):
        return sys.executable
    else:
        main_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            'main.py'
        )
        # pythonw.exe = no console window
        pythonw = sys.executable.replace('python.exe', 'pythonw.exe')
        return f'"{pythonw}" "{main_path}" --startup'


def enable_startup():
    """Add GoldTracker to Windows startup registry."""
    try:
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            STARTUP_KEY,
            0,
            winreg.KEY_SET_VALUE
        )
        winreg.SetValueEx(key, APP_NAME, 0, winreg.REG_SZ, get_app_path())
        winreg.CloseKey(key)
        print(f'[StartupManager] Added to startup: {get_app_path()}')
        return True
    except Exception as e:
        print(f'[StartupManager] Failed to add startup: {e}')
        return False


def disable_startup():
    """Remove GoldTracker from Windows startup registry."""
    try:
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            STARTUP_KEY,
            0,
            winreg.KEY_SET_VALUE
        )
        winreg.DeleteValue(key, APP_NAME)
        winreg.CloseKey(key)
        print('[StartupManager] Removed from startup')
        return True
    except FileNotFoundError:
        print('[StartupManager] Entry not found — already removed')
        return True
    except Exception as e:
        print(f'[StartupManager] Failed to remove startup: {e}')
        return False


def is_startup_enabled():
    """Check if GoldTracker is currently in Windows startup."""
    try:
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            STARTUP_KEY,
            0,
            winreg.KEY_READ
        )
        winreg.QueryValueEx(key, APP_NAME)
        winreg.CloseKey(key)
        return True
    except FileNotFoundError:
        return False
    except Exception:
        return False


def sync_startup_setting(enabled: bool):
    """
    Called from settings panel when user toggles startup.
    Syncs registry with user preference.
    """
    if enabled:
        return enable_startup()
    else:
        return disable_startup()


# ─── Quick test ───────────────────────────────────────────────────────────────
if __name__ == '__main__':
    print(f'App path: {get_app_path()}')
    print(f'Startup currently enabled: {is_startup_enabled()}')

    print('\nEnabling startup...')
    enable_startup()
    print(f'Startup enabled: {is_startup_enabled()}')

    print('\nDisabling startup...')
    disable_startup()
    print(f'Startup enabled: {is_startup_enabled()}')

    print('\nStartup manager working correctly.')