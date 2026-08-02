#!/usr/bin/env python3
"""Cheap liveness probe for outer-loop observation of a teamflow run.

Resolves the current phase receipt under <runs-dir>/<run-id>/ and prints one
line: state=<alive|exited|unknown> activity=<Ns> fp=<phase:status:size>.
Reads only phase-receipt metadata; never reads inner-loop data.
"""
import argparse
import json
import os
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


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--runs-dir", default=".teamflow/runs/code")
    parser.add_argument("--pid", type=int)
    args = parser.parse_args()

    current = Path(args.runs_dir) / args.run_id / "current.json"
    if not current.is_file():
        print("state=unknown activity=- fp=-")
        return 0
    try:
        pointer = json.loads(current.read_text(encoding="utf-8"))
        phase_path = Path(pointer["path"])
        data = json.loads(phase_path.read_text(encoding="utf-8"))
        st = phase_path.stat()
    except (OSError, KeyError, ValueError):
        print("state=unknown activity=- fp=-")
        return 0

    phase = data.get("phase", "?")
    status = data.get("status", "?")
    size = st.st_size
    activity = max(0, int(time.time() - st.st_mtime))

    if args.pid is not None:
        state = "alive" if _process_alive(args.pid) else "exited"
    else:
        state = "alive" if status == "RUNNING" else "exited"

    print(f"state={state} activity={activity}s fp={phase}:{status}:{size}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
