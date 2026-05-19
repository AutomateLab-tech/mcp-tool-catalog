"""Merge validated candidate sets into final servers-final.json."""
import json
from pathlib import Path

D = Path(__file__).parent

a = json.loads((D / "servers-v1.validated.json").read_text(encoding="utf-8"))
b = json.loads((D / "servers-npm-api.json").read_text(encoding="utf-8"))

by_pkg = {}
for r in a:
    pkg = r.get("package")
    if pkg:
        by_pkg[pkg] = r

for r in b:
    pkg = r.get("package")
    if not pkg or pkg in by_pkg:
        continue
    by_pkg[pkg] = r

merged = list(by_pkg.values())
print(f"merged: {len(merged)} (v1.validated={len(a)}, npm-api={len(b)})")

# Filter out obvious non-MCP / SDK packages that managed to leak into npm-api results
SKIP_TOKENS = ("@anthropic-ai/sdk", "@modelcontextprotocol/sdk", "@modelcontextprotocol/inspector",
               "@modelcontextprotocol/server-template", "@modelcontextprotocol/create-server")
filtered = [r for r in merged if r["package"] not in SKIP_TOKENS]
print(f"after sdk-skip: {len(filtered)}")

(D / "servers-final.json").write_text(json.dumps(filtered, indent=2), encoding="utf-8")
print("written: servers-final.json")
