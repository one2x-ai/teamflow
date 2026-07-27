#!/usr/bin/env python3
"""Remove only disposable raw Teamflow run output."""

import argparse
import shutil
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    runs = args.root.resolve() / ".teamflow" / "runs"
    if not runs.is_dir():
        return

    targets = sorted(
        path for path in runs.rglob("*")
        if path.is_file() and path.suffix in {".ndjson", ".log"}
    )
    adapter = runs / "server-scope-adapter"
    for path in targets:
        print(f"{'would remove' if args.dry_run else 'removed'} {path}")
        if not args.dry_run:
            path.unlink()
    if adapter.is_dir():
        print(f"{'would remove' if args.dry_run else 'removed'} {adapter}")
        if not args.dry_run:
            shutil.rmtree(adapter)


if __name__ == "__main__":
    main()
