#!/usr/bin/env python3
"""Cheap liveness probe for outer-loop observation of a teamflow run.

With no arguments, discovers the newest run under the runs-dir by directory
mtime.  Resolves the current phase receipt and prints one line:
state=<alive|exited|unknown> activity=<Ns> fp=<phase:status:size>.
Exit codes: 0 alive, 1 exited, 2 unknown.  Reads only phase-receipt
metadata; never reads inner-loop data.
"""
import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path


def _process_alive(pid):
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False


def _discover_run_id(runs_dir):
    base = Path(runs_dir)
    if not base.is_dir():
        return None
    candidates = [p for p in base.iterdir() if p.is_dir()]
    if not candidates:
        return None
    candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return candidates[0].name


def _pi_running():
    override = os.environ.get("TEAMFLOW_PROBE_PI_RUNNING")
    if override == "1":
        return True
    if override == "0":
        return False
    try:
        result = subprocess.run(
            ["ps", "-eo", "comm="],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    for line in result.stdout.decode("utf-8", "replace").splitlines():
        if line.strip() == "pi":
            return True
    return False


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--runs-dir", default=".teamflow/runs/code")
    parser.add_argument("--pid", type=int)
    args = parser.parse_args()

    run_id = args.run_id
    if run_id is None:
        run_id = _discover_run_id(args.runs_dir)

    EXIT = {"alive": 0, "exited": 1, "unknown": 2}

    if run_id is None:
        print("state=unknown activity=- fp=-")
        return EXIT["unknown"]

    current = Path(args.runs_dir) / run_id / "current.json"
    if not current.is_file():
        print("state=unknown activity=- fp=-")
        return EXIT["unknown"]
    try:
        pointer = json.loads(current.read_text(encoding="utf-8"))
        phase_path = Path(pointer["path"])
        data = json.loads(phase_path.read_text(encoding="utf-8"))
        st = phase_path.stat()
    except (OSError, KeyError, ValueError):
        print("state=unknown activity=- fp=-")
        return EXIT["unknown"]

    phase = data.get("phase", "?")
    status = data.get("status", "?")
    size = st.st_size
    activity = max(0, int(time.time() - st.st_mtime))

    if args.pid is not None:
        state = "alive" if _process_alive(args.pid) else "exited"
    elif status == "RUNNING":
        pi = _pi_running()
        if pi is True:
            state = "alive"
        elif pi is False:
            state = "exited"
        else:
            state = "unknown"
    elif status in ("PASS", "FAIL", "BLOCKED"):
        state = "exited"
    else:
        state = "unknown"

    print(f"state={state} activity={activity}s fp={phase}:{status}:{size}")
    return EXIT[state]


if __name__ == "__main__":
    sys.exit(main())
