"""
Validates that each package in a server list actually exists on npm.
Uses `npm view <pkg> name version` -- fast, no install.
Output: split input into <input>.validated.json and <input>.missing.json
"""
import json
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).parent
INPUT = sys.argv[1] if len(sys.argv) > 1 else "servers-all.json"
CONCURRENCY = int(os.environ.get("CONCURRENCY", "12"))

servers = json.loads((ROOT / INPUT).read_text())


def check(server):
    pkg = server["package"]
    try:
        r = subprocess.run(
            ["npm", "view", pkg, "name", "version"],
            capture_output=True, text=True, timeout=20,
            shell=(os.name == "nt"),
        )
        if r.returncode == 0 and pkg in r.stdout:
            # parse "name version" output
            lines = [l for l in r.stdout.strip().split("\n") if l]
            version = None
            for line in lines:
                if line.startswith(pkg + "@") or line.startswith("version"):
                    if "@" in line:
                        version = line.split("@", 1)[1].split(" ")[0]
                    break
            return {"server": server, "exists": True, "version": version}
        elif "E404" in (r.stderr or "") or "404" in (r.stdout or ""):
            return {"server": server, "exists": False, "reason": "404_not_found"}
        else:
            return {"server": server, "exists": False, "reason": (r.stderr or r.stdout).strip()[:200]}
    except subprocess.TimeoutExpired:
        return {"server": server, "exists": False, "reason": "npm_view_timeout"}
    except Exception as e:
        return {"server": server, "exists": False, "reason": f"exception:{e}"}


def main():
    results = []
    with ThreadPoolExecutor(max_workers=CONCURRENCY) as ex:
        futures = {ex.submit(check, s): s for s in servers}
        done = 0
        for fut in as_completed(futures):
            r = fut.result()
            results.append(r)
            done += 1
            mark = "OK" if r["exists"] else "MISSING"
            v = r.get("version") or r.get("reason", "")
            print(f"[{done}/{len(servers)}] {mark:7} {r['server']['package']:50} {v[:60]}", flush=True)

    validated = []
    missing = []
    for r in results:
        s = dict(r["server"])
        if r["exists"]:
            s["_npm_version"] = r.get("version")
            validated.append(s)
        else:
            s["_missing_reason"] = r.get("reason")
            missing.append(s)

    stem = Path(INPUT).stem
    (ROOT / f"{stem}.validated.json").write_text(json.dumps(validated, indent=2))
    (ROOT / f"{stem}.missing.json").write_text(json.dumps(missing, indent=2))
    print(f"\nValidated: {len(validated)}/{len(servers)} packages exist on npm")
    print(f"Output: {stem}.validated.json + {stem}.missing.json")


if __name__ == "__main__":
    main()
