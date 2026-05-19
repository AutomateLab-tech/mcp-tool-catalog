"""Introspect a small servers list and APPEND results to existing data/servers.jsonl
and data/tools.jsonl (without overwriting). Use to add late-arriving servers (e.g. our own)."""
import json
import os
import sys
from pathlib import Path

# Reuse introspect.py implementation by importing it
os.environ.setdefault("SERVERS_FILE", sys.argv[1] if len(sys.argv) > 1 else "servers-al.json")
os.environ.setdefault("CONCURRENCY", "2")
os.environ.setdefault("TIMEOUT", "120")

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

import introspect as ix  # noqa: E402
from concurrent.futures import ThreadPoolExecutor, as_completed
import time

print(f"appending {len(ix.SERVERS)} servers from {os.environ['SERVERS_FILE']}")
t0 = time.time()
results = []
with ThreadPoolExecutor(max_workers=int(os.environ["CONCURRENCY"])) as ex:
    futures = {ex.submit(ix.introspect, s): s for s in ix.SERVERS}
    for fut in as_completed(futures):
        results.append(fut.result())

print(f"wall: {round(time.time()-t0,1)}s")

server_rows, tool_rows = [], []
for r in results:
    server_rows.append({k: v for k, v in r.items() if k != "tools"})
    for tool in r.get("tools", []):
        tool_rows.append({
            "server_name": r["name"],
            "package": r["package"],
            "tool_name": tool.get("name"),
            "tool_description": tool.get("description"),
            "input_schema": tool.get("inputSchema"),
        })

DATA = ROOT / "data"
existing_servers = (DATA / "servers.jsonl").read_text(encoding="utf-8") if (DATA / "servers.jsonl").exists() else ""
existing_tools = (DATA / "tools.jsonl").read_text(encoding="utf-8") if (DATA / "tools.jsonl").exists() else ""

# Drop any prior rows for the same packages so we get one row per pkg
keep_servers = []
target_pkgs = {s["package"] for s in ix.SERVERS}
for line in existing_servers.splitlines():
    if not line.strip(): continue
    row = json.loads(line)
    if row.get("package") not in target_pkgs:
        keep_servers.append(line)

keep_tools = []
for line in existing_tools.splitlines():
    if not line.strip(): continue
    row = json.loads(line)
    if row.get("package") not in target_pkgs:
        keep_tools.append(line)

new_servers = keep_servers + [json.dumps(r) for r in server_rows]
new_tools = keep_tools + [json.dumps(r) for r in tool_rows]

(DATA / "servers.jsonl").write_text("\n".join(new_servers) + "\n", encoding="utf-8")
(DATA / "tools.jsonl").write_text("\n".join(new_tools) + "\n", encoding="utf-8")

ok = sum(1 for r in server_rows if r["status"] == "ok")
print(f"appended: {ok}/{len(server_rows)} OK, {len(tool_rows)} tools added")
print(f"servers.jsonl now {len(new_servers)} rows; tools.jsonl now {len(new_tools)} rows")
