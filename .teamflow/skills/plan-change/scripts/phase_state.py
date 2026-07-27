#!/usr/bin/env python3
import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path


def write(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Record or inspect a teamflow code phase")
    sub = parser.add_subparsers(dest="command", required=True)
    start = sub.add_parser("start")
    start.add_argument("--run-id", required=True)
    start.add_argument("--phase", required=True)
    start.add_argument("--owner", required=True)
    finish = sub.add_parser("finish")
    finish.add_argument("--run-id", required=True)
    finish.add_argument("--status", required=True, choices=("PASS", "FAIL", "BLOCKED"))
    finish.add_argument("--summary", required=True)
    status = sub.add_parser("status")
    status.add_argument("--run-id", required=True)
    status.add_argument("--phase")
    args = parser.parse_args()
    run_dir = Path(".teamflow/runs/code") / args.run_id
    current_path = run_dir / "current.json"
    now = datetime.now(timezone.utc).isoformat()
    if args.command == "start":
        value = {
            "schema_version": 1,
            "run_id": args.run_id,
            "phase": args.phase,
            "owner": args.owner,
            "status": "RUNNING",
            "started_at": now,
        }
        path = run_dir / "phases" / f"{args.phase}.json"
        write(path, value)
        write(current_path, {"phase": args.phase, "path": str(path)})
    elif args.command == "finish":
        current = json.loads(current_path.read_text(encoding="utf-8"))
        path = Path(current["path"])
        value = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {
            "schema_version": 1,
            "run_id": args.run_id,
        }
        value.update({"status": args.status, "summary": args.summary, "finished_at": now})
        write(path, value)
    else:
        if args.phase:
            path = run_dir / "phases" / f"{args.phase}.json"
        else:
            current = json.loads(current_path.read_text(encoding="utf-8"))
            path = Path(current["path"])
        value = json.loads(path.read_text(encoding="utf-8"))
        if value.get("status") == "RUNNING" and value.get("started_at"):
            started = datetime.fromisoformat(value["started_at"])
            age = int((datetime.now(timezone.utc) - started).total_seconds())
            value["age_seconds"] = age
            timeout = os.environ.get("TEAMFLOW_PHASE_TIMEOUT_SECONDS", "600")
            value["stale"] = age > int(timeout)
    print(json.dumps(value, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
