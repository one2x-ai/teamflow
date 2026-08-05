#!/usr/bin/env python3
"""Remove only disposable raw Teamflow run output.

Deletes raw traces, stage logs, and captured screenshots under
.teamflow/runs/, plus the temporary server-scope-adapter scratch directory.
Verified artifacts — task receipts, test patches, handoff receipts, and
memory-pipeline stage JSON — are evidence and always survive.

A finished run's coordination scratch also goes: once its root handoff has a
terminal status, the run's events/, tmp/, and liveness/ directories are
regenerable bookkeeping that nobody will read again. A run still in flight
keeps all three, because deleting the sequence counter or a sentinel under a
live process would corrupt its state.

Python bytecode caches under .teamflow/ are regenerable build scratch and go
too. The scan never leaves .teamflow/: caches elsewhere in the repository and
under the user's home directory (shared memory) always survive.
"""

import argparse
import json
import shutil
from pathlib import Path


# Raw, regenerable output. Screenshots are capture scratch from manual UI
# checks; the .json/.md artifacts under runs/ are evidence and never listed.
DISPOSABLE_SUFFIXES = {".ndjson", ".log", ".png", ".jpg", ".jpeg", ".gif", ".webp"}
DISPOSABLE_DIRECTORIES = ("server-scope-adapter",)

# Per-run coordination scratch, disposable only after the run is finished.
FINISHED_RUN_SCRATCH = ("events", "tmp", "liveness")


def _run_is_finished(run_dir: Path) -> bool:
    """True when the run has a root handoff and nothing is in flight."""
    handoffs = run_dir / "handoffs"
    if not handoffs.is_dir():
        return False
    active = run_dir / "active"
    if active.is_dir() and any(active.iterdir()):
        return False
    saw_root = False
    for state_path in sorted(handoffs.glob("*/state.json")):
        try:
            state = json.loads(state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return False
        if state.get("status") not in ("done", "blocked"):
            return False
        if not (state.get("lineage") or {}).get("parent_handoff_id"):
            saw_root = True
    return saw_root


def _finished_run_scratch(runs: Path):
    code = runs / "code"
    if not code.is_dir():
        return
    for run_dir in sorted(code.iterdir()):
        if not run_dir.is_dir() or run_dir.name.startswith("_"):
            continue
        if not _run_is_finished(run_dir):
            continue
        for name in FINISHED_RUN_SCRATCH:
            directory = run_dir / name
            if directory.is_dir():
                yield directory


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    runs = args.root.resolve() / ".teamflow" / "runs"
    if not runs.is_dir():
        return

    verb = "would remove" if args.dry_run else "removed"

    for path in sorted(
        candidate for candidate in runs.rglob("*")
        if candidate.is_file() and candidate.suffix.lower() in DISPOSABLE_SUFFIXES
    ):
        print(f"{verb} {path}")
        if not args.dry_run:
            path.unlink()

    directories = [runs / name for name in DISPOSABLE_DIRECTORIES]
    directories.extend(_finished_run_scratch(runs))
    # Python bytecode caches anywhere below .teamflow/ are regenerable; the
    # rglob stays scoped to .teamflow/ so caches outside it are never touched.
    directories.extend(
        sorted(
            candidate for candidate in (args.root.resolve() / ".teamflow").rglob("__pycache__")
            if candidate.is_dir()
        )
    )
    for directory in directories:
        if directory.is_dir():
            print(f"{verb} {directory}")
            if not args.dry_run:
                shutil.rmtree(directory)


if __name__ == "__main__":
    main()
