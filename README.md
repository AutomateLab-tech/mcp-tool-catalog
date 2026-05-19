# mcp-tool-catalog

Pipeline that builds the [`automatelab/mcp-servers-tool-catalog`](https://huggingface.co/datasets/automatelab/mcp-servers-tool-catalog) HuggingFace dataset.

The dataset is a machine-introspected catalog of public Model Context Protocol (MCP) servers and the tools each one exposes. We don't scrape READMEs — we spawn each server over stdio, send a JSON-RPC `tools/list`, and capture the real response.

| | |
|---|---|
| Dataset | [HuggingFace](https://huggingface.co/datasets/automatelab/mcp-servers-tool-catalog) |
| Companion site | [automatelab.tech/products/datasets/mcp-tool-catalog/](https://automatelab.tech/products/datasets/mcp-tool-catalog/) |
| Servers in latest snapshot | 922 attempted, 359 successfully introspected |
| Tools captured | 9,922 |
| Refresh | Monthly via GitHub Actions (1st of month, 03:00 UTC) |
| License | Code: MIT. Data: CC-BY-4.0 |

## Pipeline

```
servers-final.validated.json   → candidate npm packages (curated seed list)
        │
        ▼
validate_npm.py                → drop hallucinated packages
        │
        ▼
introspect.py                  → spawn each server, call tools/list
        │
        ├── data/servers.jsonl  → one row per server (incl. failed)
        └── data/tools.jsonl    → one row per (server, tool) on success
        │
        ▼
diff_snapshot.py               → diff vs previous month's snapshot
        │
        ▼
package_dataset.py             → write hf-dataset/{data/*.parquet, README.md}
        │
        ▼
huggingface-cli upload         → push to automatelab/mcp-servers-tool-catalog
```

## How a refresh works (monthly)

The [`.github/workflows/monthly-refresh.yml`](.github/workflows/monthly-refresh.yml) workflow runs on the 1st of every month at 03:00 UTC, or on-demand via `workflow_dispatch`:

1. Checks out the repo (previous month's `data/*.jsonl` is the snapshot).
2. Installs Node 20 + Python 3.11.
3. Runs `validate_npm.py` to catch packages that disappeared from npm.
4. Runs `introspect.py` with `CONCURRENCY=8 TIMEOUT=120` on the full validated list.
5. Diffs vs the snapshot → `changelogs/changelog-YYYY-MM-DD.md`.
6. Packages parquet + dataset card via `package_dataset.py`.
7. Uploads `hf-dataset/` to HuggingFace using `HF_TOKEN` secret.
8. Commits new `data/*.jsonl` + changelog back to `main`.

Budget: ~3 hours of runner time for the introspection step.

## Run locally

```bash
pip install -r requirements.txt

# Full refresh (mirrors the cron):
./monthly_refresh.sh

# Just rebuild the parquet card from existing data/:
python package_dataset.py
```

Introspection spawns up to `CONCURRENCY` npm processes in parallel; each gets `TIMEOUT` seconds. The pipeline is Windows-friendly (UTF-8 forced, threaded non-blocking stderr reader).

## Adding new servers to the catalog

Edit `servers-final.validated.json`:

```json
[
  { "name": "stripe", "package": "@stripe/mcp", "category": "payments" }
]
```

`validate_npm.py` will reject any package that 404s on `npm view`. The next refresh picks up the addition.

## Failure classification

Each non-`ok` server gets one of these `status` values, derived by stderr pattern matching in `introspect.py`:

- `npm_404_not_found` — package gone from npm
- `npm_network_error` — transient install failure
- `needs_<vendor>_token` / `needs_<vendor>_key` — credentials required at init
- `needs_database_url` / `needs_aws_creds` / `needs_azure_creds` / `needs_google_creds` — backing service auth
- `needs_config_file` / `needs_cli_args` — server expects positional input
- `needs_python` — Python runtime missing
- `broken_install` — install succeeded but server crashes on load
- `init_timeout` — no response to `initialize`
- `tools_list_timeout` — `initialize` ok but `tools/list` hung

## Repository layout

```
.
├── .github/workflows/monthly-refresh.yml   ← the cron
├── servers-final.validated.json             ← candidate seed list (one entry per server)
├── validate_npm.py                          ← npm existence check
├── introspect.py                            ← stdio JSON-RPC client + classifier
├── introspect_append.py                     ← retry-only helper
├── retry_failed.py                          ← re-introspect just the failures
├── merge_candidates.py                      ← merge multiple candidate sources
├── diff_snapshot.py                         ← month-to-month changelog
├── package_dataset.py                       ← parquet + dataset card
├── monthly_refresh.sh                       ← orchestrator (local + CI use the same script)
├── requirements.txt                         ← Python deps
├── data/
│   ├── servers.jsonl                        ← rolling state (committed each refresh)
│   └── tools.jsonl
├── hf-dataset/                              ← HF upload bundle (regenerated each refresh)
│   └── README.md                            ← dataset card
└── changelogs/                              ← one file per refresh
```

## License

MIT for the pipeline code (this repo). CC-BY-4.0 for the dataset rows on HuggingFace.
