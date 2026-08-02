"""Requirement regression tests for run-id ``cold-memory-regression-fix-20260728``.

These tests pin the cold-memory regression fix that:

  * Renames ``basic-memory-adapter.ts`` -> ``file-cold-store.ts``
    (class ``BasicMemoryAdapter`` -> ``FileColdStore``).
  * Relocates the cold store from ``knowledge/`` to ``state/cold-store/``.
  * Derives the repository slug from Git via ``resolveRepositorySlug``.
  * Changes scope defaults away from ``"default"`` (repository uses a
    Git-derived slug; taskId falls back to ``"_adhoc"``).

All paths are relative to the repository root
``ROOT = Path(__file__).resolve().parents[3]``.
"""

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]

EXTENSION_FILE = ROOT / ".teamflow" / "extensions" / "memory-context" / "index.ts"
MIGRATE_SCRIPT = ROOT / "scripts" / "migrate-cold-store.sh"
README_FILE = ROOT / "README.md"


# --------------------------------------------------------------------
# ColdStoreRootTests -- source assertions on index.ts
# --------------------------------------------------------------------


class ColdStoreRootTests(unittest.TestCase):
    """Verify index.ts no longer references the old adapter and uses
    the new root path, repository slug, and non-default task scope."""

    def setUp(self):
        self.text = (
            EXTENSION_FILE.read_text(encoding="utf-8")
            if EXTENSION_FILE.is_file()
            else ""
        )

    def test_index_does_not_import_basic_memory_adapter(self):
        self.assertNotIn(
            "./basic-memory-adapter",
            self.text,
            "index.ts must not import './basic-memory-adapter'",
        )

    def test_index_imports_file_cold_store(self):
        self.assertIn(
            "./file-cold-store",
            self.text,
            "index.ts must import './file-cold-store'",
        )

    def test_default_root_not_in_knowledge(self):
        m = re.search(
            r"TEAMFLOW_COLD_MEMORY_ROOT.*?;", self.text, re.DOTALL
        )
        self.assertIsNotNone(
            m,
            "index.ts must reference TEAMFLOW_COLD_MEMORY_ROOT",
        )
        block = m.group(0)
        self.assertIn(
            "cold-store",
            block,
            "default root must use 'cold-store'",
        )
        self.assertIn(
            "state",
            block,
            "default root must be under 'state'",
        )
        self.assertNotIn(
            "knowledge",
            block,
            "default root must NOT use 'knowledge'",
        )

    def test_no_repository_default_literal(self):
        pat = r'repository\s*:\s*process\.env\.TEAMFLOW_REPOSITORY\s*\|\|\s*"default"'
        self.assertIsNone(
            re.search(pat, self.text),
            "repository scope must not default to \"default\"",
        )

    def test_no_taskid_default_literal(self):
        pat = r'taskId\s*:\s*process\.env\.TEAMFLOW_TASK_ID\s*\|\|\s*"default"'
        self.assertIsNone(
            re.search(pat, self.text),
            "taskId must not default to \"default\"",
        )

    def test_calls_resolve_repository_slug(self):
        self.assertIn(
            "resolveRepositorySlug",
            self.text,
            "index.ts must call resolveRepositorySlug",
        )

    def test_taskid_not_default(self):
        self.assertTrue(
            "_adhoc" in self.text
            or not re.search(
                r'taskId\s*:\s*process\.env\.TEAMFLOW_TASK_ID\s*\|\|\s*"default"',
                self.text,
            ),
            "taskId must fall back to '_adhoc' or another non-default value",
        )


# --------------------------------------------------------------------
# MigrationScriptTests
# --------------------------------------------------------------------


class MigrationScriptTests(unittest.TestCase):
    """The one-off cold-store migration is retired.

    The defect it repaired (raw XML turns written under knowledge/ by the
    pre-rename adapter) is fixed in file-cold-store, which writes only to
    state/cold-store/. The migration script is therefore removed rather
    than kept as permanent surface. The invariant it protected is still
    asserted by ColdStoreRootTests.test_default_root_not_in_knowledge in
    this module.
    """

    def test_migration_script_is_removed(self):
        self.assertFalse(
            MIGRATE_SCRIPT.exists(),
            "the one-off scripts/migrate-cold-store.sh must be removed",
        )

    def test_no_script_references_cold_store_migration(self):
        scripts_dir = ROOT / "scripts"
        for path in sorted(scripts_dir.iterdir()):
            if not path.is_file():
                continue
            with self.subTest(script=path.name):
                self.assertNotIn(
                    "migrate-cold-store",
                    path.read_text(encoding="utf-8"),
                )


# --------------------------------------------------------------------
# ReadmeRegressionTests
# --------------------------------------------------------------------


class ReadmeRegressionTests(unittest.TestCase):
    """Verify README.md reflects the rename and relocation."""

    def setUp(self):
        self.text = (
            README_FILE.read_text(encoding="utf-8")
            if README_FILE.is_file()
            else ""
        )

    def test_readme_mentions_file_cold_store(self):
        self.assertTrue(
            "file-cold-store" in self.text
            or "FileColdStore" in self.text,
            "README must mention file-cold-store / FileColdStore",
        )

    def test_readme_documents_state_cold_store(self):
        lower = self.text.lower()
        self.assertIn("state", lower)
        self.assertIn("cold-store", lower)

    def test_readme_no_basic_memory_adapter_claim(self):
        self.assertNotIn(
            "basic-memory-adapter.ts",
            self.text,
            "README must not reference basic-memory-adapter.ts",
        )


if __name__ == "__main__":
    unittest.main()
