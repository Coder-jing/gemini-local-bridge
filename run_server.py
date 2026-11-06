#!/usr/bin/env python3
"""Standalone launcher for gemini-local-bridge.

Usage:
    python run_server.py                 # stdio (for Gemini CLI)
    python run_server.py --http          # streamable-HTTP on 127.0.0.1:8765
    python run_server.py --http --port 9000
    python run_server.py --config my.json
    python run_server.py --init-config   # write a default config and exit
"""

from __future__ import annotations

import argparse
import sys

from gemini_local_bridge import Config, create_server
from gemini_local_bridge.config import write_default_config
from gemini_local_bridge.server import run_http_with_auth


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="gemini-local-bridge",
        description="Bridge Gemini to local files, shell, and system info over MCP.",
    )
    p.add_argument("--config", help="Path to a JSON config file.")
    p.add_argument(
        "--transport",
        choices=["stdio", "streamable-http", "sse"],
        help="Override the transport from the config file.",
    )
    p.add_argument("--host", help="Override bind host (HTTP transports).")
    p.add_argument("--port", type=int, help="Override bind port (HTTP transports).")
    p.add_argument(
        "--root",
        action="append",
        dest="roots",
        help="Add an allowed root directory (repeatable).",
    )
    p.add_argument(
        "--init-config",
        action="store_true",
        help="Write a default config to gemini_bridge.config.json and exit.",
    )
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    # Double-click (no arguments) => open the desktop console. Explicit flags
    # (e.g. --transport stdio for Gemini CLI, --init-config) keep headless mode.
    if argv is None and len(sys.argv) == 1:
        from gui import main as gui_main

        gui_main()
        return 0

    if args.init_config:
        path = write_default_config("gemini_bridge.config.json")
        print(f"Wrote default config to {path}")
        return 0

    cfg = Config.load(args.config)

    # CLI overrides beat the config file.
    if args.transport:
        cfg.transport = args.transport
    if args.host:
        cfg.host = args.host
    if args.port:
        cfg.port = args.port
    if args.roots:
        cfg.allowed_roots = list(args.roots)

    server = create_server(cfg)

    print(
        f"[gemini-local-bridge] transport={cfg.transport} "
        f"roots={[str(r) for r in cfg.resolved_roots()]}",
        file=sys.stderr,
    )
    if cfg.transport == "stdio":
        server.run("stdio")
    elif cfg.transport == "sse":
        print(
            f"[gemini-local-bridge] SSE on http://{cfg.host}:{cfg.port}/sse",
            file=sys.stderr,
        )
        server.run("sse")
    else:
        print(
            f"[gemini-local-bridge] HTTP on http://{cfg.host}:{cfg.port}/mcp",
            file=sys.stderr,
        )
        # Route through our wrapper so token auth + /healthz get installed.
        run_http_with_auth(server, cfg)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())