#!/usr/bin/env python3
"""Helpers for exposing the bridge as a remote MCP via Cloudflare Tunnel.

You have a Cloudflare-hosted domain. The cleanest way to give Gemini web a
stable ``https://mcp.your-domain.com/mcp`` endpoint — without opening firewall
ports or touching your router — is a Cloudflare Tunnel (``cloudflared``).

This script wraps the common setup steps:

    python remote_mcp.py gen-token          # print a fresh bearer token
    python remote_mcp.py serve              # start bridge (reads token from env/config)
    python remote_mcp.py tunnel             # print the cloudflared command to run
    python remote_mcp.py print-connect      # print the exact URL to paste into Gemini

Prerequisites:
  * A Cloudflare account with your domain added (zone active).
  * ``cloudflared`` installed:  https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/
  * ``cloudflared tunnel login`` done once.

Typical flow (two terminals):
  Terminal A:  set GEMINI_BRIDGE_TOKEN=<token>  &&  python remote_mcp.py serve
  Terminal B:  cloudflared tunnel --url http://localhost:8765
               -> copy the https://<something>.trycloudflare.com URL it prints
  (For a custom hostname like mcp.your-domain.com, create a named tunnel and a
   DNS route — see README "Remote access via Cloudflare".)
"""

from __future__ import annotations

import argparse
import os
import sys

from gemini_local_bridge import Config
from gemini_local_bridge.auth import generate_token, load_token


def _resolve(cfg: Config, args) -> tuple[str, int, str | None]:
    host = args.host or cfg.host
    port = args.port or cfg.port
    token = load_token(cfg.auth_token)
    return host, port, token


def cmd_gen_token(_args) -> int:
    tok = generate_token()
    print(tok)
    print(
        "\n# Put this in the environment before starting the server:\n"
        f"#   PowerShell:  $env:GEMINI_BRIDGE_TOKEN = \"{tok}\"\n"
        f"#   cmd:         set GEMINI_BRIDGE_TOKEN={tok}\n"
        f"#   bash:        export GEMINI_BRIDGE_TOKEN=\"{tok}\"\n",
        file=sys.stderr,
    )
    return 0


def cmd_serve(args) -> int:
    """Start the bridge over streamable-HTTP (the remote transport)."""
    from gemini_local_bridge import create_server
    from gemini_local_bridge.server import run_http_with_auth

    cfg = Config.load(args.config)
    host, port, _ = _resolve(cfg, args)
    cfg.transport = "streamable-http"
    cfg.host = host
    cfg.port = port

    if not load_token(cfg.auth_token):
        print(
            "[warn] No GEMINI_BRIDGE_TOKEN set and auth_token empty — the /mcp "
            "endpoint will be OPEN. Set a token for any non-localhost exposure.",
            file=sys.stderr,
        )

    server = create_server(cfg)
    run_http_with_auth(server, cfg)
    return 0


def cmd_tunnel(args) -> int:
    host, port, _ = _resolve(Config.load(args.config), args)
    print(
        "Quick test tunnel (temporary *.trycloudflare.com URL):\n\n"
        f"  cloudflared tunnel --url http://{host}:{port}\n\n"
        "Named tunnel with your own hostname (mcp.your-domain.com):\n\n"
        "  cloudflared tunnel login\n"
        "  cloudflared tunnel create gemini-local-bridge\n"
        "  cloudflared tunnel route dns gemini-local-bridge mcp.your-domain.com\n"
        "  cloudflared tunnel run --url http://"
        f"{host}:{port} gemini-local-bridge\n"
    )
    return 0


def cmd_print_connect(args) -> int:
    host, port, token = _resolve(Config.load(args.config), args)
    base = args.public_url.rstrip("/")
    suffix = f"?token={token}" if token else ""
    print(base + "/mcp" + suffix)
    if not token:
        print(
            "\n# NOTE: no token configured — endpoint is open. Set "
            "GEMINI_BRIDGE_TOKEN.",
            file=sys.stderr,
        )
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="remote_mcp",
        description="Expose gemini-local-bridge as a remote MCP via Cloudflare.",
    )
    p.add_argument("--config", help="Path to a JSON config file.")
    p.add_argument("--host", help="Override bind host.")
    p.add_argument("--port", type=int, help="Override bind port.")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("gen-token", help="Generate a fresh bearer token.").set_defaults(
        func=cmd_gen_token
    )
    sub.add_parser("serve", help="Start the HTTP bridge for remote access.").set_defaults(
        func=cmd_serve
    )
    sub.add_parser("tunnel", help="Print cloudflared commands.").set_defaults(
        func=cmd_tunnel
    )
    sub.add_parser("print-connect", help="Print the URL to paste into Gemini.").set_defaults(
        func=cmd_print_connect
    )
    sub.choices["print-connect"].add_argument(
        "--public-url",
        required=True,
        help="Your public base URL, e.g. https://mcp.your-domain.com",
    )

    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())