"""End-to-end test of the PUBLIC remote MCP endpoint via Cloudflare.

Usage: python e2e_public_test.py
Env: TOKEN (required), URL (default https://gb.tinybase.site)
"""

import json
import os
import sys

import httpx

BASE = os.environ.get("URL", "https://gb.tinybase.site").rstrip("/")
TOKEN = os.environ.get("TOKEN", "")
ROOT = os.environ.get("ROOT", ".")


def main() -> int:
    headers = {
        "Accept": "application/json, text/event-stream",
        "Content-Type": "application/json",
        "Authorization": f"Bearer {TOKEN}",
    }
    failures = []

    def check(label, cond):
        print(("PASS" if cond else "FAIL"), label)
        if not cond:
            failures.append(label)

    with httpx.Client(base_url=BASE, headers=headers, timeout=30) as c:

        def post(payload):
            return c.post("/mcp", content=json.dumps(payload))

        def call_tool(rpc_id, name, args):
            r = post(
                {
                    "jsonrpc": "2.0",
                    "id": rpc_id,
                    "method": "tools/call",
                    "params": {"name": name, "arguments": args},
                }
            )
            return r.status_code, r.text

        # healthz (no token)
        hz = c.get("/healthz")
        check("healthz reachable (no token)", hz.status_code == 200)

        # no token -> 401
        anon = httpx.Client(base_url=BASE, timeout=30)
        r = anon.post(
            "/mcp",
            content=json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 0,
                    "method": "initialize",
                    "params": {
                        "protocolVersion": "2024-11-05",
                        "capabilities": {},
                        "clientInfo": {"name": "x", "version": "1"},
                    },
                }
            ),
        )
        check("no-token /mcp -> 401", r.status_code == 401)
        anon.close()

        # initialize + session
        r = post(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "clientInfo": {"name": "gemini-web", "version": "1"},
                },
            }
        )
        sid = r.headers.get("mcp-session-id")
        check("initialize 200 + session id", r.status_code == 200 and bool(sid))
        if sid:
            c.headers["mcp-session-id"] = sid
        post({"jsonrpc": "2.0", "method": "notifications/initialized"})

        # fs_write via public internet
        sc, body = call_tool(
            2, "fs_write", {"path": f"{ROOT}/public_e2e.txt", "content": "written from gemini web via cloudflare"}
        )
        check("fs_write over public MCP", sc == 200 and "bytes_written" in body)

        # fs_read round-trip
        sc, body = call_tool(3, "fs_read", {"path": f"{ROOT}/public_e2e.txt"})
        check("fs_read returns content", "written from gemini web" in body)

        # sandbox escape refused
        sc, body = call_tool(4, "fs_read", {"path": "../../../../etc/passwd"})
        check("sandbox escape refused", "outside the allowed roots" in body)

    if os.path.exists(f"{ROOT}/public_e2e.txt"):
        os.remove(f"{ROOT}/public_e2e.txt")

    print("\n" + ("ALL PASS" if not failures else f"FAILURES: {failures}"))
    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(main())