"""Force the cloudflared DIRECT rule into mihomo's RUNNING config.

Why this exists: Clash Verge writes `prepend-rules:` as a separate top-level
key, which mihomo does not understand (it only reads `rules:`). The service
also runs mihomo from a SYSTEM-protected runtime config we can't edit, and
PUT /configs?path=... rejects paths outside its sandbox.

Solution: read the live clash-verge.yaml, move the cloudflared entries from
`prepend-rules:` into the very top of `rules:`, then push the ENTIRE config to
mihomo as an inline `payload` via its named-pipe API. mihomo applies it
immediately — no file access needed.
"""

import importlib.util
import json
import sys

import yaml

spec = importlib.util.spec_from_file_location("acr", "apply_clash_rule.py")
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

LIVE = r"C:\Users\jingy\AppData\Roaming\io.github.clash-verge-rev.clash-verge-rev\clash-verge.yaml"

CLOUDFLARED_RULES = [
    "PROCESS-NAME,cloudflared,DIRECT",
    "PROCESS-NAME,cloudflared.exe,DIRECT",
]


def main() -> int:
    with open(LIVE, "r", encoding="utf-8") as fh:
        cfg = yaml.safe_load(fh)

    if not isinstance(cfg, dict) or "rules" not in cfg:
        print("ERROR: could not read a dict config with a rules list")
        return 1

    rules = cfg.get("rules") or []

    # Drop any existing cloudflared entries so we don't duplicate.
    rules = [r for r in rules if "cloudflared" not in str(r)]
    # Prepend ours at the very top.
    rules = CLOUDFLARED_RULES + list(rules)
    cfg["rules"] = rules

    # Remove the unprocessed prepend-rules key to avoid confusion.
    cfg.pop("prepend-rules", None)

    payload = yaml.safe_dump(cfg, allow_unicode=True, sort_keys=False)
    print(f"[build] rules now {len(rules)} entries; cloudflared at top")

    body = json.dumps({"path": "", "payload": payload})
    status, text = m.pipe_request("PUT", "/configs?force=true", body)
    print(f"[reload] PUT /configs (inline payload) -> {status}")
    if status not in (200, 201, 202, 204):
        print(f"[reload] body: {text[:400]}")
        return 1

    # Verify.
    vstatus, vtext = m.pipe_request("GET", "/rules")
    live = "cloudflared" in vtext.lower()
    print(f"[verify] GET /rules -> {vstatus}; cloudflared live: {live}")
    print("RESULT:", "OK" if live else "FAILED")
    return 0 if live else 1


if __name__ == "__main__":
    sys.exit(main())