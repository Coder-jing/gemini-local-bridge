"""Windows autostart helper: register/unregister the bridge to run at login.

Writes a ``HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Run`` value so the
console starts (minimized to tray) when you log in. Uses only the current user
hive, so no admin rights are needed.
"""

from __future__ import annotations

import os
import sys

_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
_VALUE_NAME = "GeminiLocalBridge"


def _exe_command() -> str:
    """Command line to launch the console at login."""
    if getattr(sys, "frozen", False):
        # Packaged exe: launch self with no args (opens the GUI).
        return f'"{sys.executable}"'
    # Source: launch gui.py with the current interpreter.
    gui = Path(__file__).resolve().parent.parent / "gui.py"
    return f'"{sys.executable}" "{gui}"'


def is_enabled() -> bool:
    """True if the autostart Run value exists."""
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY) as key:
            winreg.QueryValueEx(key, _VALUE_NAME)
            return True
    except (ImportError, FileNotFoundError, OSError):
        return False


def enable() -> tuple[bool, str]:
    """Register autostart. Returns (ok, message)."""
    try:
        import winreg

        cmd = _exe_command()
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, _RUN_KEY, 0, winreg.KEY_SET_VALUE
        ) as key:
            winreg.SetValueEx(key, _VALUE_NAME, 0, winreg.REG_SZ, cmd)
        return True, f"Autostart enabled: {cmd}"
    except ImportError:
        return False, "Autostart is only supported on Windows."
    except OSError as exc:
        return False, f"Could not enable autostart: {exc}"


def disable() -> tuple[bool, str]:
    """Remove the autostart Run value. Returns (ok, message)."""
    try:
        import winreg

        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, _RUN_KEY, 0, winreg.KEY_SET_VALUE
        ) as key:
            winreg.DeleteValue(key, _VALUE_NAME)
        return True, "Autostart disabled."
    except ImportError:
        return False, "Autostart is only supported on Windows."
    except FileNotFoundError:
        return True, "Autostart was not enabled."
    except OSError as exc:
        return False, f"Could not disable autostart: {exc}"