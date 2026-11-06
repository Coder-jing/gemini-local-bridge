"""Cloudflared tunnel manager for the desktop console.

Starts/stops a ``cloudflared`` tunnel as a child process and reports whether it
has registered connections with the Cloudflare edge (i.e. the public URL is
live). Reads the tunnel UUID + config from ``%USERPROFILE%\\.cloudflared``.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import threading
import time
from pathlib import Path

# Public hostnames -> tunnel id, parsed from a gembridge-style config.
_INGRESS_RE = re.compile(r"hostname:\s*(\S+)")


class TunnelManager:
    """Manage a named cloudflared tunnel as a child process."""

    def __init__(self, config_path: str | None = None) -> None:
        self.config_path = Path(
            config_path
            or os.environ.get("CLOUDFLARED_CONFIG")
            or Path.home() / ".cloudflared" / "gembridge.yml"
        )
        self._proc: subprocess.Popen | None = None
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self.hostname: str | None = None
        self.tunnel_id: str | None = None
        self._parse_config()

    def _parse_config(self) -> None:
        """Extract tunnel id and first ingress hostname from the config."""
        if not self.config_path.is_file():
            return
        try:
            text = self.config_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return
        m = re.search(r"tunnel:\s*([0-9a-f-]{36})", text)
        if m:
            self.tunnel_id = m.group(1)
        h = _INGRESS_RE.search(text)
        if h:
            self.hostname = h.group(1)

    @staticmethod
    def available() -> bool:
        """True if cloudflared is installed and on PATH."""
        return shutil.which("cloudflared") is not None

    @property
    def running(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    @property
    def public_url(self) -> str | None:
        return f"https://{self.hostname}" if self.hostname else None

    def start(self, protocol: str = "http2") -> tuple[bool, str]:
        """Start the tunnel. Returns (ok, message)."""
        with self._lock:
            if self.running:
                return True, "Tunnel already running."
            if not self.available():
                return False, "cloudflared is not installed or not on PATH."
            if not self.config_path.is_file() or not self.tunnel_id:
                return False, f"Tunnel config not found: {self.config_path}"

            cmd = [
                "cloudflared",
                "tunnel",
                "--config",
                str(self.config_path),
                "--protocol",
                protocol,
                "run",
                self.tunnel_id,
            ]
            try:
                # Hide the console window on Windows.
                creationflags = (
                    getattr(subprocess, "CREATE_NO_WINDOW", 0)
                    if os.name == "nt"
                    else 0
                )
                self._proc = subprocess.Popen(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    creationflags=creationflags,
                )
            except OSError as exc:
                return False, f"Failed to launch cloudflared: {exc}"

            self._thread = threading.Thread(
                target=self._drain_output, daemon=True
            )
            self._thread.start()
            return True, "Tunnel starting..."

    def _drain_output(self) -> None:
        """Consume cloudflared stdout so the pipe doesn't fill and block."""
        proc = self._proc
        if not proc or not proc.stdout:
            return
        for _line in iter(proc.stdout.readline, ""):
            if proc.poll() is not None:
                break

    def stop(self) -> None:
        with self._lock:
            if self._proc and self._proc.poll() is None:
                self._proc.terminate()
                try:
                    self._proc.wait(timeout=8)
                except subprocess.TimeoutExpired:
                    self._proc.kill()
            self._proc = None
            self._thread = None

    def wait_until_live(self, timeout: float = 20.0) -> bool:
        """Block until the tunnel registers with the edge (or timeout)."""
        # cloudflared prints "Registered tunnel connection" when live; we can't
        # easily intercept that from the drain thread, so poll the process and
        # give the edge a moment. Callers typically retry the public URL.
        deadline = time.time() + timeout
        while time.time() < deadline:
            if not self.running:
                return False
            time.sleep(1.0)
        return self.running