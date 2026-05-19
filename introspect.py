"""
Pilot MCP introspector. Spawns each server via `npx -y <package>` over stdio,
performs JSON-RPC handshake, captures tools/list response, writes one row per tool.

Usage: python introspect.py
Output:
  data/tools.jsonl   one row per (server, tool)
  data/servers.jsonl one row per server (with status, tool_count, error if any)
  logs/<server>.log  raw stderr per server
"""
import json
import os
import queue
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).parent
SERVERS_FILE = os.environ.get("SERVERS_FILE", "servers.json")
CONCURRENCY = int(os.environ.get("CONCURRENCY", "1"))
SERVERS = json.loads((ROOT / SERVERS_FILE).read_text())
DATA = ROOT / "data"
LOGS = ROOT / "logs"
DATA.mkdir(exist_ok=True)
LOGS.mkdir(exist_ok=True)

TIMEOUT = int(os.environ.get("TIMEOUT", "120"))  # seconds per server


def jsonrpc(method, params=None, id=None):
    msg = {"jsonrpc": "2.0", "method": method}
    if params is not None:
        msg["params"] = params
    if id is not None:
        msg["id"] = id
    return json.dumps(msg) + "\n"


FAILURE_PATTERNS = [
    (r"npm error code E404|npm error 404 Not Found", "npm_404_not_found"),
    (r"npm error code ECONNRESET|npm error code ETIMEDOUT|npm error network", "npm_network_error"),
    (r"npm error.*ELIFECYCLE|npm error.*EEXIST|npm error.*EACCES", "npm_install_error"),
    (r"GITHUB_PERSONAL_ACCESS_TOKEN|GITHUB_TOKEN.*required|GitHub token.*required", "needs_github_token"),
    (r"STRIPE_SECRET_KEY|Stripe API key not provided", "needs_stripe_key"),
    (r"BRAVE_API_KEY", "needs_brave_key"),
    (r"GOOGLE.*API_KEY|GOOGLE_MAPS_API_KEY|GOOGLE_APPLICATION_CREDENTIALS", "needs_google_creds"),
    (r"OPENAI_API_KEY", "needs_openai_key"),
    (r"ANTHROPIC_API_KEY", "needs_anthropic_key"),
    (r"SLACK_BOT_TOKEN|SLACK_USER_TOKEN", "needs_slack_token"),
    (r"DATABASE_URL|POSTGRES_URL|MYSQL_URL|MONGODB_URI|MONGO_URL", "needs_database_url"),
    (r"AWS_ACCESS_KEY_ID|AWS_SECRET_ACCESS_KEY", "needs_aws_creds"),
    (r"AZURE.*KEY|AZURE.*TOKEN|AZURE.*TENANT", "needs_azure_creds"),
    (r"Config file not found|config\.json not found|configuration file.*missing", "needs_config_file"),
    (r"Required command was not provided|missing required argument|Usage: |error: required option", "needs_cli_args"),
    (r"environment variable is required|environment variable is not set|environment variable.*missing", "needs_env_var"),
    (r"gcloud executable not found|kubectl not found|docker not running|docker daemon not", "needs_external_runtime"),
    (r"Run .* --setup|Run .* --help|please configure|configuration required", "needs_setup_step"),
    (r"Cannot find module|MODULE_NOT_FOUND", "broken_install"),
    (r"requires Python|python3 not found|spawn python ENOENT", "needs_python"),
    (r"Permission denied|EACCES", "permission_error"),
    (r"npm error.*complete log", "npm_install_generic"),
]

import re

def classify_stderr(log_path):
    """Read stderr log and match against known failure patterns. Returns (reason, snippet) or (None, None)."""
    try:
        text = log_path.read_text(encoding="utf-8", errors="replace")
    except FileNotFoundError:
        return None, None
    for pattern, reason in FAILURE_PATTERNS:
        m = re.search(pattern, text, re.IGNORECASE)
        if m:
            # find the line containing the match
            line_start = text.rfind("\n", 0, m.start()) + 1
            line_end = text.find("\n", m.end())
            snippet = text[line_start: line_end if line_end != -1 else len(text)].strip()[:200]
            return reason, snippet
    # default: last non-empty line as snippet
    lines = [l.strip() for l in text.strip().split("\n") if l.strip()]
    snippet = lines[-1][:200] if lines else None
    return None, snippet


def introspect(server):
    name = server["name"]
    pkg = server["package"]
    args = server.get("args", [])
    env = {**os.environ, **server.get("env", {})}
    log_path = LOGS / f"{name}.log"

    cmd = ["npx", "-y", pkg, *args]
    print(f"[{name}] spawning: {' '.join(cmd)}", flush=True)
    t0 = time.time()

    try:
        proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=open(log_path, "wb"),
            env=env,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            shell=(os.name == "nt"),
        )
    except Exception as e:
        return {"name": name, "package": pkg, "status": "spawn_error", "reason": "spawn_error", "error": str(e), "tools": []}

    reader_state = {}
    try:
        # handshake
        proc.stdin.write(jsonrpc("initialize", {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "al-171-pilot", "version": "0.1"}
        }, id=1))
        proc.stdin.flush()
        init_resp = read_response(proc, timeout=TIMEOUT, proc_log=log_path, _reader_state=reader_state)
        if not init_resp:
            return finish_with_reason(proc, name, pkg, "init_timeout", t0, log_path)

        proc.stdin.write(jsonrpc("notifications/initialized"))
        proc.stdin.flush()

        proc.stdin.write(jsonrpc("tools/list", {}, id=2))
        proc.stdin.flush()
        tools_resp = read_response(proc, timeout=TIMEOUT, want_id=2, proc_log=log_path, _reader_state=reader_state)
        if not tools_resp:
            return finish_with_reason(proc, name, pkg, "tools_list_timeout", t0, log_path)

        tools = tools_resp.get("result", {}).get("tools", [])
        elapsed = round(time.time() - t0, 2)
        finish_proc(proc)
        print(f"[{name}] OK {len(tools)} tools in {elapsed}s", flush=True)
        return {
            "name": name,
            "package": pkg,
            "status": "ok",
            "elapsed_s": elapsed,
            "tool_count": len(tools),
            "tools": tools,
            "server_info": init_resp.get("result", {}).get("serverInfo"),
        }
    except Exception as e:
        finish_proc(proc)
        reason, snippet = classify_stderr(log_path)
        return {"name": name, "package": pkg, "status": "error", "reason": reason or "exception", "error": str(e), "stderr_snippet": snippet, "tools": []}


def finish_with_reason(proc, name, pkg, status, t0, log_path):
    finish_proc(proc)
    reason, snippet = classify_stderr(log_path)
    elapsed = round(time.time() - t0, 2)
    final_status = reason if reason else status
    print(f"[{name}] FAIL {final_status} in {elapsed}s -- {snippet or ''}", flush=True)
    return {
        "name": name,
        "package": pkg,
        "status": final_status,
        "raw_status": status,
        "elapsed_s": elapsed,
        "stderr_snippet": snippet,
        "tools": [],
    }


def _stdout_reader(proc, q, stop):
    """Background thread: pump stdout lines into a queue. Exits when proc closes stdout or stop set."""
    try:
        for line in proc.stdout:
            if stop.is_set():
                return
            q.put(line)
    except Exception:
        pass
    finally:
        q.put(None)  # sentinel: EOF


def read_response(proc, timeout, want_id=None, proc_log=None, _reader_state=None):
    """Read JSON-RPC responses line-by-line until matching id, EOF, or deadline.
    Uses a background reader thread so we never block past the deadline (Windows readline can hang).
    Re-entrant: pass _reader_state dict to share the reader across calls."""
    deadline = time.time() + timeout
    early_exit_grace = 2.0
    proc_exit_time = None

    if _reader_state is None:
        _reader_state = {}
    q = _reader_state.get("q")
    if q is None:
        q = queue.Queue()
        stop = threading.Event()
        t = threading.Thread(target=_stdout_reader, args=(proc, q, stop), daemon=True)
        t.start()
        _reader_state["q"] = q
        _reader_state["stop"] = stop
        _reader_state["thread"] = t

    while True:
        remaining = deadline - time.time()
        if remaining <= 0:
            return None
        rc = proc.poll()
        if rc is not None:
            if proc_exit_time is None:
                proc_exit_time = time.time()
            elif time.time() - proc_exit_time > early_exit_grace:
                return None
        try:
            line = q.get(timeout=min(remaining, 0.25))
        except queue.Empty:
            continue
        if line is None:
            return None  # EOF
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        if "id" in msg and (want_id is None or msg["id"] == want_id):
            return msg


def finish_proc(proc):
    try:
        proc.terminate()
        proc.wait(timeout=3)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass


def main():
    server_rows = []
    tool_rows = []
    t_start = time.time()
    write_lock = threading.Lock()
    servers_path = DATA / "servers.jsonl"
    tools_path = DATA / "tools.jsonl"
    # Resume support: skip packages already in servers.jsonl
    done_pkgs = set()
    if servers_path.exists():
        for line in servers_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                done_pkgs.add(json.loads(line).get("package"))
            except json.JSONDecodeError:
                pass
    todo = [s for s in SERVERS if s["package"] not in done_pkgs]
    print(f"resume: {len(done_pkgs)} already done, {len(todo)} to do")

    def persist(result):
        with write_lock:
            row = {k: v for k, v in result.items() if k != "tools"}
            with open(servers_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(row) + "\n")
            if result.get("tools"):
                with open(tools_path, "a", encoding="utf-8") as f:
                    for tool in result["tools"]:
                        f.write(json.dumps({
                            "server_name": result["name"],
                            "package": result["package"],
                            "tool_name": tool.get("name"),
                            "tool_description": tool.get("description"),
                            "input_schema": tool.get("inputSchema"),
                        }) + "\n")
            server_rows.append(row)
            for tool in result.get("tools", []):
                tool_rows.append(tool)

    if CONCURRENCY <= 1:
        for s in todo:
            persist(introspect(s))
    else:
        with ThreadPoolExecutor(max_workers=CONCURRENCY) as ex:
            futures = {ex.submit(introspect, s): s for s in todo}
            for fut in as_completed(futures):
                try:
                    persist(fut.result())
                except Exception as e:
                    print(f"[exec] thread error: {e}", flush=True)
    print(f"Wall time: {round(time.time() - t_start, 1)}s with concurrency={CONCURRENCY}")

    ok = sum(1 for r in server_rows if r["status"] == "ok")
    from collections import Counter
    status_counts = Counter(r["status"] for r in server_rows)
    print(f"\nSummary: {ok}/{len(server_rows)} servers OK, {len(tool_rows)} tools total")
    print(f"Status breakdown: {dict(status_counts)}")
    print(f"Output: {DATA}")


if __name__ == "__main__":
    main()
