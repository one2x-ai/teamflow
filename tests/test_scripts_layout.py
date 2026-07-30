"""Requirement tests for the scripts/ verb-naming layout and uninstall.

Contracts:

1. Verb-named scripts: install, uninstall, setup, update, clean, plus the
   existing bootstrap.sh / doctor.sh / teamflow launcher. Old names must be
   gone so there is exactly one spelling per operation.
2. The one-off migrate-cold-store.sh is removed: its defect was fixed in
   file-cold-store and no data remains to migrate.
3. uninstall removes global install traces by default (launcher command and,
   only when explicitly asked, the shared memory data) and can additionally
   clean a target project's .teamflow/ plus its .gitignore entry when
   --project is supplied.
4. uninstall is safe: it never deletes user memory without an explicit flag,
   never touches a launcher it does not own, and supports --dry-run.
"""

import os
import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"

NEW_NAMES = ("install.sh", "uninstall.sh", "setup.sh", "update.sh", "clean.py")
RETIRED_NAMES = (
    "init-project.sh",
    "setup-memory.sh",
    "update-basic-memory-skills.sh",
    "teamflow-prune-runs",
    "migrate-cold-store.sh",
)


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.is_file() else ""


class VerbNamedScriptsTests(unittest.TestCase):
    """Contract 1: every operation has one verb-named entry point."""

    def test_new_scripts_exist_and_are_executable(self):
        for name in NEW_NAMES:
            with self.subTest(script=name):
                path = SCRIPTS / name
                self.assertTrue(path.is_file(), f"scripts/{name} must exist")
                self.assertTrue(
                    os.access(path, os.X_OK), f"scripts/{name} must be executable"
                )

    def test_retained_scripts_still_exist(self):
        for name in ("bootstrap.sh", "doctor.sh", "teamflow"):
            with self.subTest(script=name):
                self.assertTrue(
                    (SCRIPTS / name).is_file(), f"scripts/{name} must be retained"
                )

    def test_retired_names_are_gone(self):
        for name in RETIRED_NAMES:
            with self.subTest(script=name):
                self.assertFalse(
                    (SCRIPTS / name).exists(),
                    f"scripts/{name} must be renamed or removed",
                )

    def test_no_source_file_references_retired_names(self):
        """Docs, scripts, and tests must use the new spellings only."""
        searched = [
            *sorted(SCRIPTS.iterdir()),
            ROOT / "README.md",
            ROOT / "AGENTS.md",
            ROOT / ".teamflow" / "AGENTS.md",
        ]
        for path in searched:
            if not path.is_file():
                continue
            text = read(path)
            for name in RETIRED_NAMES:
                with self.subTest(file=path.name, retired=name):
                    self.assertNotIn(
                        name, text, f"{path.name} still references {name}"
                    )


class MigrateColdStoreRemovedTests(unittest.TestCase):
    """Contract 2: the one-off cold-store migration is retired."""

    def test_migrate_script_is_removed(self):
        self.assertFalse(
            (SCRIPTS / "migrate-cold-store.sh").exists(),
            "the one-off migrate-cold-store.sh must be removed",
        )

    def test_no_script_mentions_cold_store_migration(self):
        for path in sorted(SCRIPTS.iterdir()):
            if not path.is_file():
                continue
            with self.subTest(script=path.name):
                self.assertNotIn("migrate-cold-store", read(path))


class UninstallScopeTests(unittest.TestCase):
    """Contract 3: uninstall cleans global traces, optionally a project."""

    def setUp(self):
        self.text = read(SCRIPTS / "uninstall.sh")

    def test_removes_global_launcher(self):
        self.assertRegex(
            self.text,
            r"TEAMFLOW_BIN_DIR|\.local/bin",
            "uninstall must target the global launcher directory",
        )
        self.assertIn(
            "agent-teamflow-launcher",
            self.text,
            "uninstall must verify launcher ownership before removing it",
        )

    def test_supports_project_option(self):
        self.assertIn(
            "--project",
            self.text,
            "uninstall must accept --project to clean a target project",
        )

    def test_project_cleanup_removes_teamflow_dir(self):
        self.assertRegex(
            self.text,
            r"\.teamflow",
            "uninstall --project must remove the project .teamflow/ directory",
        )

    def test_project_cleanup_removes_gitignore_entry(self):
        self.assertIn(
            ".gitignore",
            self.text,
            "uninstall --project must remove the installer's .gitignore entry",
        )

    def test_memory_removal_is_opt_in(self):
        """Shared memory is user data: never removed without an explicit flag."""
        self.assertIn(
            "--memory",
            self.text,
            "uninstall must gate memory deletion behind an explicit --memory flag",
        )
        memory_flag_index = self.text.index("--memory")
        self.assertRegex(
            self.text[memory_flag_index:],
            r"TEAMFLOW_MEMORY_HOME|memory",
            "the --memory flag must target the shared memory root",
        )


class UninstallSafetyTests(unittest.TestCase):
    """Contract 4: uninstall is non-surprising and reversible in intent."""

    def setUp(self):
        self.text = read(SCRIPTS / "uninstall.sh")

    def test_has_usage_help(self):
        self.assertRegex(self.text, r"-h\|--help|--help\)")
        self.assertIn("Usage:", self.text)

    def test_supports_dry_run(self):
        self.assertIn(
            "--dry-run",
            self.text,
            "uninstall must support --dry-run",
        )

    def test_uses_strict_bash_mode(self):
        self.assertRegex(self.text, r"set -euo pipefail")

    def test_does_not_remove_unowned_launcher(self):
        """A launcher without the teamflow marker must be left alone."""
        self.assertRegex(
            self.text,
            r"grep -q ['\"]?agent-teamflow-launcher",
            "uninstall must check the launcher marker before deleting",
        )

    def test_never_hard_removes_home_root_unconditionally(self):
        """No unguarded rm -rf of $HOME or the teamflow home root."""
        self.assertNotRegex(
            self.text,
            r'rm -rf "\$HOME"(?!/)',
            "uninstall must never remove $HOME",
        )
        self.assertNotRegex(
            self.text,
            r'rm -rf "\$TEAMFLOW_HOME"\s*$',
            "uninstall must not unconditionally remove the whole teamflow home",
        )


class CleanScriptTests(unittest.TestCase):
    """clean keeps its disposable-only deletion contract after the rename."""

    def setUp(self):
        self.text = read(SCRIPTS / "clean.py")

    def test_deletes_only_disposable_suffixes(self):
        self.assertIn(".ndjson", self.text)
        self.assertIn(".log", self.text)

    def test_supports_dry_run(self):
        self.assertIn("--dry-run", self.text)

    def test_scopes_to_teamflow_runs(self):
        self.assertRegex(self.text, r'"\.teamflow"\s*/\s*"runs"|\.teamflow.*runs')


if __name__ == "__main__":
    unittest.main()
