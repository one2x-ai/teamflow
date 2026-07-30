#!/usr/bin/env python3
"""Remove only disposable raw Teamflow run output.

Deletes raw traces, stage logs, and captured screenshots under
.teamflow/runs/, plus the temporary server-scope-adapter scratch directory.
Verified artifacts — task receipts, test patches, and memory-pipeline stage
JSON — are evidence and always survive.
"""

import argparse
import shutil
from pathlib import Path


# Raw, regenerable output. Screenshots are capture scratch from manual UI
# checks; the .json/.md artifacts under runs/ are evidence and never listed.
DISPOSABLE_SUFFIXES = {".ndjson", ".log", ".png", ".jpg", ".jpeg", ".gif", ".webp"}
DISPOSABLE_DIRECTORIES = ("server-scope-adapter",)


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

    for name in DISPOSABLE_DIRECTORIES:
        directory = runs / name
        if directory.is_dir():
            print(f"{verb} {directory}")
            if not args.dry_run:
                shutil.rmtree(directory)


if __name__ == "__main__":
    main()
