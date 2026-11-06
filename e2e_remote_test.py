"""End-to-end remote MCP client test against the packaged exe over HTTP.

Run the exe first (see README), then: python e2e_remote_test.py
"""

import json
import os
import sys

import httpx

BASE = os.environ.get("BRIDGE_URL", "http://127.0.0.1:8799")
TOKEN = os.environ.get("TOKEN", "")


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

    # A single Client = a single persistent connection, which is what ties the
    # MCP streamable-http session together (a real browser/Gemini behaves this
    # way; connection-per-request clients see "Session not found").
    with httpx.Client(base_url=BASE, headers=headers, timeout=15) as client:

        def post(payload):
            r = client.post("/mcp", content=json.dumps(payload))
            return r

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

        # 1. initialize
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
        # propagate session id on subsequent requests (httpx keeps connection,
        # but the id must be echoed too)
        if sid:
            client.headers["mcp-session-id"] = sid

        # 2. initialized notification
        post({"jsonrpc": "2.0", "method": "notifications/initialized"})

        # 3. real tool call: fs_write
        sc, body = call_tool(2, "fs_write", {"path": "e2e.txt", "content": "hello from gemini web"})
        # The MCP result wraps our tool JSON inside a text content block, so the
        # fields appear escaped/without spaces (e.g. "isError":false). Match on
        # the field names, which are present regardless of escaping/spacing.
        check(
            "fs_write over remote HTTP",
            sc == 200 and "bytes_written" in body and '"isError":false' in body,
        )

        # 4. fs_read round-trip
        sc, body = call_tool(3, "fs_read", {"path": "e2e.txt"})
        check("fs_read returns written content", "hello from gemini web" in body)

        # 5. sandbox escape refused
        sc, body = call_tool(4, "fs_read", {"path": "../../../../etc/passwd"})
        check("sandbox escape refused", "outside the allowed roots" in body)

        # 6. dangerous shell command denied
        cmd = "del e2e.txt" if os.name == "nt" else "rm e2e.txt"
        sc, body = call_tool(5, "shell_run", {"command": cmd})
        check("dangerous shell command denied", "denied by policy" in body)

    if os.path.exists("e2e.txt"):
        os.remove("e2e.txt")

    print("\n" + ("ALL PASS" if not failures else f"FAILURES: {failures}"))
    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(main())