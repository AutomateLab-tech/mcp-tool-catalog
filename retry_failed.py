"""Retry init_timeout / npm_install_generic / tools_list_timeout failures at low concurrency, long timeout.
Replaces prior failed rows in servers.jsonl + tools.jsonl with new results (one row per pkg).
"""
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

# Retry classes that benefit from more time / less contention
RETRY_STATUSES = {"init_timeout", "npm_install_generic", "tools_list_timeout", "error"}

ROOT = Path(__file__).parent
DATA = ROOT / "data"
servers_path = DATA / "servers.jsonl"
tools_path = DATA / "tools.jsonl"

# Load existing
existing_servers = []
for line in servers_path.read_text(encoding="utf-8").splitlines():
    if not line.strip():
        continue
    existing_servers.append(json.loads(line))
existing_tools = []
for line in tools_path.read_text(encoding="utf-8").splitlines():
    if not line.strip():
        continue
    existing_tools.append(json.loads(line))

# Find packages to retry
retry_pkgs = [s for s in existing_servers if s.get("status") in RETRY_STATUSES]
print(f"retry candidates: {len(retry_pkgs)} (of {len(existing_servers)} total)")

# Build introspect specs from the original final list
spec_path = ROOT / "servers-final.validated.json"
spec = json.loads(spec_path.read_text(encoding="utf-8"))
by_pkg = {s["package"]: s for s in spec}
retry_specs = []
for r in retry_pkgs:
    pkg = r["package"]
    if pkg in by_pkg:
        retry_specs.append(by_pkg[pkg])
    else:
        # Recreate minimal spec for packages not in validated json (e.g. automatelab ones)
        retry_specs.append({"name": r["name"], "package": pkg, "args": [], "env": {}})
print(f"retry specs: {len(retry_specs)}")

# Reuse introspect.py logic but with longer timeout
os.environ["TIMEOUT"] = "180"
os.environ["SERVERS_FILE"] = "servers-final.validated.json"  # required by introspect.py import
sys.path.insert(0, str(ROOT))
import introspect as ix  # noqa: E402

# Override TIMEOUT in the module
ix.TIMEOUT = 180

CONCURRENCY = int(os.environ.get("RETRY_CONCURRENCY", "3"))

t0 = time.time()
results = []
with ThreadPoolExecutor(max_workers=CONCURRENCY) as ex:
    futures = {ex.submit(ix.introspect, s): s for s in retry_specs}
    for fut in as_completed(futures):
        results.append(fut.result())

elapsed = round(time.time() - t0, 1)
print(f"wall: {elapsed}s for {len(results)} retries")

# Stats
from collections import Counter
new_status = Counter(r["status"] for r in results)
fixed = sum(1 for r in results if r["status"] == "ok")
print(f"newly OK: {fixed}/{len(results)}")
for k, v in new_status.most_common():
    print(f"  {k}: {v}")

# Merge: drop old rows for these packages, replace with new
retry_pkg_set = {r["package"] for r in results}

keep_servers = [s for s in existing_servers if s["package"] not in retry_pkg_set]
keep_tools = [t for t in existing_tools if t["package"] not in retry_pkg_set]

new_server_rows = []
new_tool_rows = []
for r in results:
    new_server_rows.append({k: v for k, v in r.items() if k != "tools"})
    for tool in r.get("tools", []):
        new_tool_rows.append({
            "server_name": r["name"],
            "package": r["package"],
            "tool_name": tool.get("name"),
            "tool_description": tool.get("description"),
            "input_schema": tool.get("inputSchema"),
        })

final_servers = keep_servers + new_server_rows
final_tools = keep_tools + new_tool_rows

# Write atomically: tmp then move
(DATA / "servers.jsonl.tmp").write_text(
    "\n".join(json.dumps(r) for r in final_servers) + "\n", encoding="utf-8"
)
(DATA / "tools.jsonl.tmp").write_text(
    "\n".join(json.dumps(r) for r in final_tools) + "\n", encoding="utf-8"
)
os.replace(DATA / "servers.jsonl.tmp", servers_path)
os.replace(DATA / "tools.jsonl.tmp", tools_path)

final_ok = sum(1 for s in final_servers if s["status"] == "ok")
print(f"\nfinal: {final_ok}/{len(final_servers)} OK ({100*final_ok/len(final_servers):.1f}%)")
print(f"tools: {len(final_tools)}")
