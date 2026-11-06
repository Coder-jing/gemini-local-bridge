"""Diagnostic middleware: log every MCP request's method, path, status and the
key headers that determine whether auth / DNS-rebinding checks pass.

Added temporarily to diagnose why Gemini web reports "MCP server could not be
reached" while raw httpx tests pass. Logs to gemini_bridge_requests.log next to
the exe / working directory.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from starlette.types import ASGIApp, Message, Receive, Scope, Send

LOG_PATH = Path("gemini_bridge_requests.log")


def _log(entry: dict) -> None:
    try:
        with LOG_PATH.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError:
        pass


class RequestDiagnosticMiddleware:
    """ASGI middleware capturing request + response status for diagnosis."""

    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope.get("headers", [])}
        path = scope.get("path", "")
        method = scope.get("method", "")

        status_holder = {"status": None}
        start = time.time()

        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                status_holder["status"] = message["status"]
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            _log(
                {
                    "ts": time.strftime("%H:%M:%S"),
                    "method": method,
                    "path": path,
                    "status": status_holder["status"],
                    "ms": round((time.time() - start) * 1000),
                    "host": headers.get("host"),
                    "origin": headers.get("origin"),
                    "auth": ("present" if headers.get("authorization") else "MISSING"),
                    "token_q": ("yes" if "token=" in (scope.get("query_string", b"").decode("latin-1")) else "no"),
                    "content_type": headers.get("content-type"),
                    "accept": headers.get("accept"),
                    "user_agent": headers.get("user-agent"),
                }
            )