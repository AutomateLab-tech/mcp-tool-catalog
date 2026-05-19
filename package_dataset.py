"""
Convert tools.jsonl + servers.jsonl into a HuggingFace-ready dataset directory.

Output (tmp/mcp-tool-catalog/hf-dataset/):
  data/tools.parquet         one row per (server, tool)
  data/servers.parquet       one row per server (including failed ones, with reason)
  README.md                  dataset card with motivation, schema, comparison
  dataset_infos.json         dataset metadata

Does NOT upload -- run `huggingface-cli upload` manually after review.
"""
import json
import os
import sys
from datetime import date
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).parent
SRC = ROOT / "data"
OUT = ROOT / "hf-dataset"
OUT.mkdir(exist_ok=True)
(OUT / "data").mkdir(exist_ok=True)


def load_jsonl(path):
    return [json.loads(l) for l in open(path, encoding="utf-8") if l.strip()]


def main():
    servers = load_jsonl(SRC / "servers.jsonl")
    tools = load_jsonl(SRC / "tools.jsonl")

    # Normalise schema. input_schema is nested -- stringify for parquet.
    for t in tools:
        t["input_schema"] = json.dumps(t.get("input_schema") or {})
    for s in servers:
        if "tools" in s:
            del s["tools"]
        if "server_info" in s and s["server_info"]:
            s["server_info"] = json.dumps(s["server_info"])

    tools_df = pd.DataFrame(tools)
    servers_df = pd.DataFrame(servers)

    tools_df.to_parquet(OUT / "data" / "tools.parquet", index=False)
    servers_df.to_parquet(OUT / "data" / "servers.parquet", index=False)

    ok = (servers_df["status"] == "ok").sum()
    total = len(servers_df)

    today = date.today().isoformat()

    card = f"""---
license: cc-by-4.0
tags:
- mcp
- model-context-protocol
- tools
- agents
- ai-agents
language:
- en
pretty_name: MCP Servers Tool Catalog
size_categories:
- 1K<n<10K
configs:
- config_name: tools
  data_files:
  - split: train
    path: data/tools.parquet
- config_name: servers
  data_files:
  - split: train
    path: data/servers.parquet
---

# MCP Servers Tool Catalog

Machine-introspected catalog of public Model Context Protocol (MCP) servers and the tools each one exposes. Each tool row contains the exact `name`, `description`, and JSON Schema `input_schema` captured by running the server and calling its `tools/list` JSON-RPC endpoint -- not metadata scraping.

**Last extracted:** {today}
**Servers attempted:** {total}
**Servers successfully introspected:** {ok}
**Tools captured:** {len(tools_df)}

## Why this dataset exists

The MCP ecosystem grew from ~1,200 servers in Q1 2025 to ~9,400 servers by April 2026, with 97M monthly SDK downloads. Every AI agent shipping MCP support (Claude Code, Cursor, Cline, Zed) faces the same problem: choosing servers blind. Existing datasets are insufficient:

| Dataset | Servers | Tool defs? | Refresh | Downloads/mo |
|---|---|---|---|---|
| ScaleAI/MCP-Atlas | 36 | yes (benchmark subset) | static | 2,588 |
| DeepNLP/mcp-servers | 296 | **metadata only** | stale since 2025-03 | ~20 |
| **automatelab/mcp-servers-tool-catalog** | **{total}** | **yes (full schemas)** | **monthly** | -- |

Three differentiators:
1. **Actual tool introspection** -- we run each server and capture its `tools/list` response, not its README.
2. **Full coverage** of npm-published MCP servers (vs. benchmark slices).
3. **Monthly refresh** with diff-based changelog (vs. static snapshots).

## Schema

### `tools` split
| field | type | description |
|---|---|---|
| `server_name` | string | short slug for the server |
| `package` | string | exact npm package name |
| `tool_name` | string | `name` field from `tools/list` |
| `tool_description` | string | `description` field from `tools/list` |
| `input_schema` | string (JSON) | `inputSchema` field, JSON-encoded for parquet |

### `servers` split
| field | type | description |
|---|---|---|
| `name` | string | short slug |
| `package` | string | npm package |
| `status` | string | `ok`, or one of the failure categories below |
| `elapsed_s` | float | wall-clock seconds for introspection |
| `tool_count` | int | tools captured (0 if not ok) |
| `server_info` | string (JSON) | server's response to `initialize` |
| `stderr_snippet` | string | last meaningful stderr line if introspection failed |
| `raw_status` | string | underlying state when classification couldn't pinpoint cause |

### Failure categories

Each non-`ok` row has a `status` from the following set, derived by matching the server's stderr against known patterns:

- `npm_404_not_found` -- package does not exist on the npm registry
- `npm_network_error` -- transient registry/network issue at install
- `needs_<vendor>_token` / `needs_<vendor>_key` -- server hard-requires real credentials at startup (e.g. `needs_stripe_key`, `needs_github_token`)
- `needs_database_url` / `needs_aws_creds` / `needs_azure_creds` / `needs_google_creds` -- backing-service credentials required
- `needs_config_file` -- server expects a config file path
- `needs_cli_args` -- server requires positional CLI arguments (e.g. a root path)
- `needs_python` -- runtime depends on Python but `python` is not on `PATH`
- `broken_install` -- npm install completed but the server fails to load (missing module, etc.)
- `init_timeout` -- spawned, but did not respond to `initialize` within timeout
- `tools_list_timeout` -- responded to `initialize` but not to `tools/list`

## Methodology

For each candidate package:
1. Verify existence with `npm view <pkg> name`.
2. Spawn `npx -y <pkg>` over stdio with a JSON-RPC client.
3. Send `initialize` (protocol version `2024-11-05`) and `notifications/initialized`.
4. Send `tools/list`, capture the response.
5. Classify stderr against ~17 known failure patterns if any step fails.
6. Write one row per server (always) and one row per tool (only on success).

Auth-gated servers were given dummy environment variables (`STRIPE_SECRET_KEY=dummy`, etc.). Most servers that list tools without hitting their backing service worked fine with dummies; those that validate credentials at `initialize` time get `needs_<vendor>_*` status.

The pipeline source is open: see `introspect.py` and `validate_npm.py` in the dataset repo.

## License

Dataset additions: CC-BY-4.0. Each tool row attributes its source `package` to the upstream MCP server repository under its own license (see `servers` split).

## Refresh cadence

Monthly. Each version tag includes a diff vs. the previous month (added servers, removed servers, new tools per server, removed tools).

## How to load

```python
from datasets import load_dataset
tools = load_dataset("automatelab/mcp-servers-tool-catalog", "tools", split="train")
servers = load_dataset("automatelab/mcp-servers-tool-catalog", "servers", split="train")
```

## Acknowledgements

Built by [automatelab](https://automatelab.tech). Companion site: [automatelab.tech/products/datasets/mcp-tool-catalog/](https://automatelab.tech/products/datasets/mcp-tool-catalog/). Pipeline source: [github.com/AutomateLab-tech/mcp-tool-catalog](https://github.com/AutomateLab-tech/mcp-tool-catalog).
"""

    (OUT / "README.md").write_text(card, encoding="utf-8")

    info = {
        "dataset_name": "mcp-servers-tool-catalog",
        "version": today.replace("-", "."),
        "servers_attempted": total,
        "servers_ok": int(ok),
        "tools_captured": int(len(tools_df)),
    }
    (OUT / "dataset_infos.json").write_text(json.dumps(info, indent=2))

    print(f"Wrote HF dataset to {OUT}")
    print(f"  tools.parquet: {len(tools_df)} rows")
    print(f"  servers.parquet: {len(servers_df)} rows ({ok} ok)")


if __name__ == "__main__":
    main()
