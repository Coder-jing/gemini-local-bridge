"""Apply the cloudflared DIRECT rule to the live mihomo config and hot-reload.

Two steps:
  1. Prepend PROCESS-NAME,cloudflared,DIRECT to the `rules:` of the live
     clash-verge.yaml (the config mihomo actually loaded).
  2. Hot-reload mihomo via its REST API served over the named pipe
     (external-controller TCP is disabled, so we speak HTTP over the pipe).

This makes the tunnel work immediately. The Merge.yaml edit is the persistent
layer that survives profile/subscription updates.
"""

import io
import json
import sys

LIVE = r"C:\Users\jingy\AppData\Roaming\io.github.clash-verge-rev.clash-verge-rev\clash-verge.yaml"
PIPE = r"\\.\pipe\verge-mihomo-production-d7f31b8d94d1ef3afa2632b128d156ffc5b5524b92075e929343a69279f1febb"

RULES = [
    "  - PROCESS-NAME,cloudflared,DIRECT",
    "  - PROCESS-NAME,cloudflared.exe,DIRECT",
]


def patch_live_config() -> bool:
    with open(LIVE, "r", encoding="utf-8") as fh:
        lines = fh.readlines()

    if any("cloudflared" in ln for ln in lines):
        print("[patch] cloudflared rule already present, skipping edit")
        return True

    out = []
    inserted = False
    for ln in lines:
        out.append(ln)
        if not inserted and ln.strip() == "rules:":
            out.extend(r + "\n" for r in RULES)
            inserted = True

    if not inserted:
        print("[patch] ERROR: could not find a top-level 'rules:' line")
        return False

    with open(LIVE, "w", encoding="utf-8") as fh:
        fh.writelines(out)
    print("[patch] inserted cloudflared DIRECT rules after 'rules:'")
    return True


def _read_http_response(pipe) -> tuple[int, str]:
    """Read one HTTP response off the pipe, returning (status, body).

    mihomo replies with chunked transfer encoding (no Content-Length), so we
    read until the connection closes and then de-chunk the body.
    """
    data = bytearray()
    while True:
        chunk = pipe.read(65536)
        if not chunk:
            break
        data.extend(chunk)
    raw = bytes(data)
    head, _, body = raw.partition(b"\r\n\r\n")
    status = 0
    if head.startswith(b"HTTP/"):
        try:
            status = int(head.split(b" ")[1])
        except (IndexError, ValueError):
            status = 0

    # De-chunk if Transfer-Encoding: chunked.
    lowered_head = head.lower()
    if b"transfer-encoding: chunked" in lowered_head:
        body = _dechunk(body)
    return status, body.decode("utf-8", errors="replace")


def _dechunk(body: bytes) -> bytes:
    """Decode an HTTP chunked body."""
    out = bytearray()
    i = 0
    while i < len(body):
        j = body.find(b"\r\n", i)
        if j == -1:
            break
        try:
            size = int(body[i:j].split(b";")[0].strip(), 16)
        except ValueError:
            break
        if size == 0:
            break
        out.extend(body[j + 2 : j + 2 + size])
        i = j + 2 + size + 2  # skip chunk data + trailing CRLF
    return bytes(out)


def pipe_request(method: str, path: str, body: str = "") -> tuple[int, str]:
    """Send one HTTP request to mihomo over its named-pipe controller."""
    pipe = open(PIPE, "r+b", buffering=0)
    try:
        payload = body.encode("utf-8")
        req = (
            f"{method} {path} HTTP/1.1\r\n"
            f"Host: mihomo\r\n"
            f"Content-Type: application/json\r\n"
            f"Content-Length: {len(payload)}\r\n"
            f"Connection: close\r\n"
            f"\r\n"
        ).encode("utf-8") + payload
        pipe.write(req)
        return _read_http_response(pipe)
    finally:
        pipe.close()


def reload() -> bool:
    body = json.dumps({"path": "", "payload": ""})
    status, text = pipe_request("PUT", "/configs?force=true", body)
    print(f"[reload] PUT /configs?force=true -> {status}")
    if text.strip():
        print(f"[reload] body: {text[:300]}")
    return status in (200, 201, 202, 204)


def main() -> int:
    ok = True
    ok &= patch_live_config()
    ok &= reload()

    # Verify the rule is now live via the API.
    try:
        status, text = pipe_request("GET", "/rules")
        live = 'cloudflared' in text
        print(f"[verify] GET /rules -> {status}; cloudflared rule live: {live}")
        ok &= live
    except Exception as exc:  # noqa: BLE001
        print(f"[verify] could not read /rules: {exc}")

    print("\nRESULT:", "OK" if ok else "FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())