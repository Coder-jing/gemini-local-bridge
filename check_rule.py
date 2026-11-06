"""Check whether the cloudflared DIRECT rule is live in mihomo's ruleset."""

import importlib.util
import json

spec = importlib.util.spec_from_file_location("acr", "apply_clash_rule.py")
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

status, text = m.pipe_request("GET", "/rules")
print("GET /rules status:", status)

# The pipe returns chunked transfer encoding (hex sizes prefixed), so the raw
# body isn't pure JSON. A substring search is robust regardless of framing.
if "cloudflared" in text.lower():
    print("RESULT: RULE LIVE (found 'cloudflared' in running ruleset)")
else:
    print("RESULT: RULE MISSING (no 'cloudflared' in running ruleset)")
    print("body head:", text[:300])