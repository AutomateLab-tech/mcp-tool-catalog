"""
Diff two tools.jsonl snapshots and print a Markdown changelog.
Usage: python diff_snapshot.py <prev.jsonl> <curr.jsonl>
"""
import json
import sys
from collections import defaultdict
from pathlib import Path

prev_path, curr_path = Path(sys.argv[1]), Path(sys.argv[2])

def index(path):
    by_server = defaultdict(set)
    if not path.exists():
        return by_server
    for line in open(path, encoding="utf-8"):
        if not line.strip():
            continue
        t = json.loads(line)
        by_server[t["server_name"]].add(t["tool_name"])
    return by_server

prev = index(prev_path)
curr = index(curr_path)

prev_servers = set(prev)
curr_servers = set(curr)

added_servers = sorted(curr_servers - prev_servers)
removed_servers = sorted(prev_servers - curr_servers)
common = sorted(curr_servers & prev_servers)

print(f"# MCP catalog changelog\n")
print(f"Generated from `{curr_path.name}` vs `{prev_path.name}`.\n")
print(f"## Summary\n")
print(f"- Servers added: **{len(added_servers)}**")
print(f"- Servers removed: **{len(removed_servers)}**")
print(f"- Servers with tool changes: see below\n")

if added_servers:
    print("## New servers this month\n")
    for s in added_servers:
        print(f"- `{s}` -- {len(curr[s])} tools")
    print()

if removed_servers:
    print("## Removed servers\n")
    for s in removed_servers:
        print(f"- `{s}` -- was {len(prev[s])} tools")
    print()

tool_changes = []
for s in common:
    added = curr[s] - prev[s]
    removed = prev[s] - curr[s]
    if added or removed:
        tool_changes.append((s, added, removed))

if tool_changes:
    print("## Tool changes on existing servers\n")
    for s, added, removed in sorted(tool_changes, key=lambda x: len(x[1]) + len(x[2]), reverse=True):
        print(f"### `{s}`")
        if added:
            print(f"  Added: {', '.join(sorted('`' + t + '`' for t in added))}")
        if removed:
            print(f"  Removed: {', '.join(sorted('`' + t + '`' for t in removed))}")
        print()
