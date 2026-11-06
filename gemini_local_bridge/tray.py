"""System tray icon for the desktop console (Windows/macOS/Linux via pystray).

Provides a small tray icon so closing the window minimizes to the tray (the
bridge keeps running) instead of exiting. The tray menu can restore the window
or fully quit.
"""

from __future__ import annotations

import threading


def _make_image():
    """Render the branded app icon (bridge glyph + sparkle) for the tray."""
    from .icon import build_image

    return build_image(64)


class TrayIcon:
    """A system tray icon bound to a tkinter root window."""

    def __init__(self, root, on_show, on_quit):
        self.root = root
        self.on_show = on_show
        self.on_quit = on_quit
        self._icon = None
        self._thread: threading.Thread | None = None

    def start(self) -> bool:
        """Create and run the tray icon on a daemon thread. Returns success."""
        try:
            import pystray
        except ImportError:
            return False

        image = _make_image()

        def show(_icon=None, item=None):
            # Marshal back to the tkinter main thread.
            self.root.after(0, self.on_show)

        def quit_app(_icon=None, item=None):
            self.root.after(0, self.on_quit)

        menu = pystray.Menu(
            pystray.MenuItem("显示控制台", show, default=True),
            pystray.MenuItem("退出", quit_app),
        )
        self._icon = pystray.Icon(
            "gemini-local-bridge", image, "Gemini Local Bridge", menu
        )
        self._thread = threading.Thread(target=self._icon.run, daemon=True)
        self._thread.start()
        return True

    def stop(self) -> None:
        if self._icon is not None:
            try:
                self._icon.stop()
            except Exception:  # noqa: BLE001
                pass
            self._icon = None