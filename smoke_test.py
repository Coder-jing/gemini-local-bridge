"""Quick end-to-end smoke test of gemini-local-bridge tool handlers.

Run with: python smoke_test.py
"""

from __future__ import annotations

import asyncio
import json
import os
import tempfile

from gemini_local_bridge import Config, create_server


async def call(server, name, args):
    """Invoke a registered tool handler through the FastMCP call path."""
    result = await server.call_tool(name, args)
    # FastMCP returns a list of content blocks; pull the structured payload.
    if isinstance(result, tuple):
        result = result[0]
    blocks = list(result)
    out = []
    for b in blocks:
        if hasattr(b, "text"):
            try:
                out.append(json.loads(b.text))
            except Exception:
                out.append(b.text)
    return out[0] if out else None


async def main() -> int:
    tmp = tempfile.mkdtemp(prefix="glb_test_")
    cfg = Config(allowed_roots=[tmp], audit_log_path=None, enable_shell=True)
    server = create_server(cfg)

    failures = []

    def check(label, cond):
        print(("PASS" if cond else "FAIL"), label)
        if not cond:
            failures.append(label)

    # 1. write + read round-trip
    r = await call(server, "fs_write", {"path": "hello.txt", "content": "line1\nline2\n"})
    check("fs_write ok", r.get("ok") and r.get("bytes_written", 0) > 0)
    r = await call(server, "fs_read", {"path": "hello.txt"})
    check("fs_read content", r.get("content") == "line1\nline2\n")

    # 2. list + tree
    r = await call(server, "fs_list", {"path": "."})
    check("fs_list has hello.txt", any(e["name"] == "hello.txt" for e in r.get("entries", [])))
    r = await call(server, "fs_tree", {"path": ".", "depth": 2})
    check("fs_tree shows hello.txt", "hello.txt" in r.get("tree", ""))

    # 3. edit
    r = await call(server, "fs_edit", {"path": "hello.txt", "old_string": "line1", "new_string": "LINE1"})
    check("fs_edit replaced", r.get("replacements") == 1)
    r = await call(server, "fs_read", {"path": "hello.txt"})
    check("fs_edit persisted", r.get("content") == "LINE1\nline2\n")

    # 4. search
    r = await call(server, "fs_search", {"root": ".", "pattern": "line2"})
    check("fs_search found", len(r.get("matches", [])) == 1)

    # 5. sandbox escape must be refused
    r = await call(server, "fs_read", {"path": os.path.expanduser("~/.ssh/id_rsa")})
    check("sandbox blocks ~/.ssh", not r.get("ok"))
    r = await call(server, "fs_read", {"path": "../../etc/passwd"})
    check("sandbox blocks ../ escape", not r.get("ok"))
    r = await call(server, "fs_write", {"path": "C:/Windows/glbevil.txt", "content": "x"})
    check("sandbox blocks abs write", not r.get("ok"))

    # 6. shell policy: echo allowed, rm denied
    if os.name == "nt":
        r = await call(server, "shell_run", {"command": "echo hi"})
        check("shell echo ok", r.get("ok") and "hi" in r.get("stdout", ""))
        r = await call(server, "shell_run", {"command": "del hello.txt"})
        check("shell del denied", not r.get("ok"))
    else:
        r = await call(server, "shell_run", {"command": "echo hi"})
        check("shell echo ok", r.get("ok") and "hi" in r.get("stdout", ""))
        r = await call(server, "shell_run", {"command": "rm hello.txt"})
        check("shell rm denied", not r.get("ok"))

    # 7. shell_python
    r = await call(server, "shell_python", {"code": "print(2+2)"})
    check("shell_python 4", r.get("ok") and "4" in r.get("stdout", ""))

    # 8. system tools
    r = await call(server, "sys_info", {})
    check("sys_info platform", bool(r.get("platform")))
    r = await call(server, "sys_disk", {"path": "."})
    check("sys_disk total", (r.get("total_bytes") or 0) > 0)

    # 9. move + copy + delete
    await call(server, "fs_copy", {"src": "hello.txt", "dst": "sub/hello2.txt"})
    r = await call(server, "fs_read", {"path": "sub/hello2.txt"})
    check("fs_copy readable", r.get("ok"))
    r = await call(server, "fs_move", {"src": "sub/hello2.txt", "dst": "sub/hello3.txt"})
    check("fs_move ok", r.get("ok"))
    r = await call(server, "fs_delete", {"path": "sub", "recursive": True})
    check("fs_delete recursive", r.get("ok"))
    r = await call(server, "fs_read", {"path": "sub/hello3.txt"})
    check("fs_delete gone", not r.get("ok"))

    print("\n" + ("ALL PASS" if not failures else f"FAILURES: {failures}"))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))