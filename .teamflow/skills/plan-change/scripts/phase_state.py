#!/usr/bin/env python3
"""Transitional alias for the retired ``phase`` receipt surface.

``teamflow handoff`` supersedes this script; it survives one version so
agent prompts written against ``teamflow phase`` keep working. The blocked
enum is imported rather than restated so the alias can never disagree with
the handoff CLI about which reasons exist.
"""
import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(
    0, str(Path(__file__).resolve().parents[2] / "write-handoff" / "scripts")
)
from handoff_state import BLOCKED_REASONS, BUDGET_REASONS  # noqa: E402


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
    start.add_argument("--parent-run-id")
    start.add_argument("--parent-phase")
    start.add_argument("--split-scope")
    finish = sub.add_parser("finish")
    finish.add_argument("--run-id", required=True)
    finish.add_argument("--status", required=True, choices=("PASS", "FAIL", "BLOCKED"))
    finish.add_argument("--summary", required=True)
    finish.add_argument("--block-reason", action="append", choices=BLOCKED_REASONS)
    finish.add_argument("--budget-limit", type=int)
    finish.add_argument("--budget-used", type=int)
    finish.add_argument("--budget-remaining", type=int)
    finish.add_argument("--protected-component")
    finish.add_argument("--required-action")
    finish.add_argument("--largest-sources", default="[]")
    finish.add_argument("--source-refs", default="[]")
    status = sub.add_parser("status")
    status.add_argument("--run-id", required=True)
    status.add_argument("--phase")
    exp = sub.add_parser("planning-experience")
    exp.add_argument("--run-id", required=True)
    exp.add_argument("--parent-run-id", required=True)
    exp.add_argument("--parent-phase", required=True)
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
        if args.parent_run_id or args.parent_phase or args.split_scope:
            value["lineage"] = {
                "parent_run_id": args.parent_run_id,
                "parent_phase": args.parent_phase,
                "split_scope": args.split_scope,
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
        if args.block_reason:
            reasons = list(args.block_reason)
            value["blocked"] = {"reason": reasons[0], "reasons": reasons}
            if any(reason in BUDGET_REASONS for reason in reasons):
                value["budget_failure"] = {
                    "reason": reasons[0],
                    "budget": {
                        "limit": args.budget_limit,
                        "used": args.budget_used,
                        "remaining": args.budget_remaining,
                    },
                    "protected_component": args.protected_component,
                    "required_action": args.required_action,
                    "largest_sources": json.loads(args.largest_sources),
                    "source_refs": json.loads(args.source_refs),
                }
        write(path, value)
    elif args.command == "planning-experience":
        phases_dir = run_dir / "phases"
        phase_files = sorted(phases_dir.glob("*.json")) if phases_dir.is_dir() else []
        if not phase_files:
            value = {"status": "deferred", "reason": "no child phases to verify"}
        else:
            children = [json.loads(p.read_text(encoding="utf-8")) for p in phase_files]
            blocking = next(
                (c for c in children if c.get("status") != "PASS"),
                None,
            )
            if blocking is not None:
                phase_name = blocking.get("phase", "unknown")
                value = {
                    "status": "deferred",
                    "reason": f"phase '{phase_name}' status is {blocking.get('status')}",
                }
            else:
                parent_path = (
                    Path(".teamflow/runs/code") / args.parent_run_id
                    / "phases" / f"{args.parent_phase}.json"
                )
                failure_mode = "unknown"
                if parent_path.is_file():
                    parent = json.loads(parent_path.read_text(encoding="utf-8"))
                    failure_mode = parent.get("budget_failure", {}).get("reason", "unknown")
                experience = {
                    "failure_mode": failure_mode,
                    "original_split": {
                        "run_id": args.parent_run_id,
                        "phase": args.parent_phase,
                    },
                    "verified_new_split": [
                        {
                            "phase": c.get("phase"),
                            "status": c.get("status"),
                            "summary": c.get("summary"),
                        }
                        for c in children
                    ],
                    "evidence_receipt_refs": [f"phases/{p.name}" for p in phase_files],
                    "applicable_scope": "planning-feedback",
                }
                path = run_dir / "planning-experience.json"
                write(path, experience)
                value = {"status": "generated", "path": str(path)}
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
