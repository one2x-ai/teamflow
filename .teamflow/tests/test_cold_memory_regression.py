"""Requirement regression tests for run-id ``cold-memory-regression-fix-20260728``.

These tests pin the cold-memory regression fix that:

  * Renames ``basic-memory-adapter.ts`` -> ``file-cold-store.ts``
    (class ``BasicMemoryAdapter`` -> ``FileColdStore``).
  * Relocates the cold store from ``knowledge/`` to ``state/cold-store/``.
  * Derives the repository slug from Git via ``resolveRepositorySlug``.
  * Changes scope defaults away from ``"default"`` (repository uses a
    Git-derived slug; taskId falls back to ``"_adhoc"``).

All paths are relative to the repository root
``ROOT = Path(__file__).resolve().parents[2]``.
"""

import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]

EXTENSION_FILE = ROOT / ".teamflow" / "extensions" / "memory-context" / "index.ts"
FILE_COLD_STORE_FILE = (
    ROOT / ".teamflow" / "extensions" / "memory-context" / "file-cold-store.ts"
)
TURN_BLOCK_FILE = (
    ROOT / ".teamflow" / "extensions" / "memory-context" / "turn-block.ts"
)
FILE_COLD_STORE_ABS = str(FILE_COLD_STORE_FILE)
TURN_BLOCK_ABS = str(TURN_BLOCK_FILE)
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
# FileColdStoreSourceTests -- source assertions on file-cold-store.ts
# --------------------------------------------------------------------


class FileColdStoreSourceTests(unittest.TestCase):
    """Verify file-cold-store.ts exists, uses the new class name, and
    exports resolveRepositorySlug."""

    def setUp(self):
        self.text = (
            FILE_COLD_STORE_FILE.read_text(encoding="utf-8")
            if FILE_COLD_STORE_FILE.is_file()
            else ""
        )

    def test_file_cold_store_exists(self):
        self.assertTrue(
            FILE_COLD_STORE_FILE.is_file(),
            "file-cold-store.ts must exist",
        )

    def test_old_adapter_removed(self):
        old = (
            ROOT
            / ".teamflow"
            / "extensions"
            / "memory-context"
            / "basic-memory-adapter.ts"
        )
        self.assertFalse(
            old.is_file(),
            "basic-memory-adapter.ts must be removed",
        )

    def test_class_name_is_file_cold_store(self):
        self.assertIn(
            "class FileColdStore",
            self.text,
            "source must define 'class FileColdStore'",
        )

    def test_class_name_not_basic_memory_adapter(self):
        self.assertNotIn(
            "class BasicMemoryAdapter",
            self.text,
            "source must not define 'class BasicMemoryAdapter'",
        )

    def test_exports_resolve_repository_slug(self):
        self.assertIn(
            "resolveRepositorySlug",
            self.text,
            "source must define resolveRepositorySlug",
        )
        self.assertTrue(
            "export function resolveRepositorySlug" in self.text
            or "export { resolveRepositorySlug" in self.text,
            "resolveRepositorySlug must be exported",
        )

    def test_docstring_not_basic_memory_adapter(self):
        head = self.text[:600].lower()
        self.assertNotIn(
            "basic memory adapter",
            head,
            "file must not self-describe as a 'Basic Memory adapter'",
        )


# --------------------------------------------------------------------
# RepositorySlugTests -- bun behavioral
# --------------------------------------------------------------------


class RepositorySlugTests(unittest.TestCase):
    """Verify resolveRepositorySlug derives slugs from Git remotes."""

    def setUp(self):
        if shutil.which("bun") is None:
            self.skipTest("bun not on PATH")

    def _run_bun_test(self, ts_code):
        """Write ts_code to a temp file, run with bun, return parsed JSON."""
        with tempfile.NamedTemporaryFile(
            suffix=".ts", mode="w", delete=False
        ) as f:
            f.write(ts_code)
            f.flush()
            tmp = f.name
        try:
            result = subprocess.run(
                ["bun", "run", tmp],
                capture_output=True,
                text=True,
                timeout=30,
            )
        finally:
            os.unlink(tmp)
        if result.returncode != 0:
            self.fail(
                f"bun exited {result.returncode}:\n"
                f"stdout={result.stdout}\nstderr={result.stderr}"
            )
        lines = [
            ln for ln in result.stdout.strip().split("\n") if ln.strip()
        ]
        return json.loads(lines[-1]) if lines else {}

    def test_env_override(self):
        data = self._run_bun_test(
            'import { resolveRepositorySlug } from "'
            + FILE_COLD_STORE_ABS
            + "\";\n"
            + 'process.env.TEAMFLOW_REPOSITORY = "myrepo";\n'
            + 'console.log(JSON.stringify({ slug: resolveRepositorySlug() }));\n'
        )
        self.assertEqual(data.get("slug"), "myrepo")

    def test_slug_from_remote_url(self):
        code = (
            'import { resolveRepositorySlug } from "'
            + FILE_COLD_STORE_ABS
            + "\";\n"
            + 'import { execSync } from "node:child_process";\n'
            + 'import * as fs from "node:fs";\n'
            + 'import * as path from "node:path";\n'
            + 'import * as os from "node:os";\n'
            + 'const dir = fs.mkdtempSync(path.join(os.tmpdir(), "slug-test-"));\n'
            + 'execSync("git init -q", { cwd: dir });\n'
            + "execSync('git remote add origin https://github.com/acme/widget.git',"
            + " { cwd: dir });\n"
            + "const slug = resolveRepositorySlug(dir);\n"
            + 'fs.rmSync(dir, { recursive: true, force: true });\n'
            + 'console.log(JSON.stringify({ slug }));\n'
        )
        data = self._run_bun_test(code)
        self.assertEqual(data.get("slug"), "widget")

    def test_slug_lowercases_and_sluggifies(self):
        code = (
            'import { resolveRepositorySlug } from "'
            + FILE_COLD_STORE_ABS
            + "\";\n"
            + 'import { execSync } from "node:child_process";\n'
            + 'import * as fs from "node:fs";\n'
            + 'import * as path from "node:path";\n'
            + 'import * as os from "node:os";\n'
            + 'const dir = fs.mkdtempSync(path.join(os.tmpdir(), "slug-test-"));\n'
            + 'execSync("git init -q", { cwd: dir });\n'
            + "execSync('git remote add origin git@github.com:acme/My-Cool_App.git',"
            + " { cwd: dir });\n"
            + "const slug = resolveRepositorySlug(dir);\n"
            + 'fs.rmSync(dir, { recursive: true, force: true });\n'
            + 'console.log(JSON.stringify({ slug }));\n'
        )
        data = self._run_bun_test(code)
        self.assertEqual(data.get("slug"), "my-cool_app")

    def test_slug_no_remote_falls_back_to_dir_basename(self):
        code = (
            'import { resolveRepositorySlug } from "'
            + FILE_COLD_STORE_ABS
            + "\";\n"
            + 'import { execSync } from "node:child_process";\n'
            + 'import * as fs from "node:fs";\n'
            + 'import * as path from "node:path";\n'
            + 'import * as os from "node:os";\n'
            + 'const dir = fs.mkdtempSync(path.join(os.tmpdir(), "slug-test-"));\n'
            + 'execSync("git init -q", { cwd: dir });\n'
            + "const slug = resolveRepositorySlug(dir);\n"
            + 'const expected = path.basename(dir).toLowerCase()'
            + '.replace(/[^a-z0-9._-]+/g, "-");\n'
            + 'fs.rmSync(dir, { recursive: true, force: true });\n'
            + 'console.log(JSON.stringify({ slug, expected }));\n'
        )
        data = self._run_bun_test(code)
        slug = data.get("slug", "")
        self.assertTrue(slug, "slug must be non-empty")
        self.assertNotEqual(slug, "default")
        self.assertEqual(slug, data.get("expected"))

    def test_slug_never_returns_default(self):
        code = (
            'import { resolveRepositorySlug } from "'
            + FILE_COLD_STORE_ABS
            + "\";\n"
            + 'import { execSync } from "node:child_process";\n'
            + 'import * as fs from "node:fs";\n'
            + 'import * as path from "node:path";\n'
            + 'import * as os from "node:os";\n'
            + 'const dir = fs.mkdtempSync(path.join(os.tmpdir(), "slug-test-"));\n'
            + 'execSync("git init -q", { cwd: dir });\n'
            + "const slug = resolveRepositorySlug(dir);\n"
            + 'fs.rmSync(dir, { recursive: true, force: true });\n'
            + 'console.log(JSON.stringify({ slug }));\n'
        )
        data = self._run_bun_test(code)
        self.assertNotEqual(data.get("slug"), "default")


# --------------------------------------------------------------------
# FileColdStoreBehavioralTests -- bun behavioral
# --------------------------------------------------------------------


class FileColdStoreBehavioralTests(unittest.TestCase):
    """Verify FileColdStore preserves hash/offset/read semantics."""

    def setUp(self):
        if shutil.which("bun") is None:
            self.skipTest("bun not on PATH")

    def _run_bun_test(self, ts_code):
        """Write ts_code to a temp file, run with bun, return parsed JSON."""
        with tempfile.NamedTemporaryFile(
            suffix=".ts", mode="w", delete=False
        ) as f:
            f.write(ts_code)
            f.flush()
            tmp = f.name
        try:
            result = subprocess.run(
                ["bun", "run", tmp],
                capture_output=True,
                text=True,
                timeout=30,
            )
        finally:
            os.unlink(tmp)
        if result.returncode != 0:
            self.fail(
                f"bun exited {result.returncode}:\n"
                f"stdout={result.stdout}\nstderr={result.stderr}"
            )
        lines = [
            ln for ln in result.stdout.strip().split("\n") if ln.strip()
        ]
        return json.loads(lines[-1]) if lines else {}

    def test_write_read_roundtrip(self):
        code = (
            'import { FileColdStore } from "'
            + FILE_COLD_STORE_ABS
            + "\";\n"
            + 'import * as os from "node:os";\n'
            + 'import * as path from "node:path";\n'
            + 'import * as fs from "node:fs";\n'
            + "(async () => {\n"
            + '  const root = fs.mkdtempSync(path.join(os.tmpdir(), "cold-"));\n'
            + "  const store = new FileColdStore(root);\n"
            + "  const block = {\n"
            + '    version: 1, id: "turn-1", sequence: 1, previous: null,\n'
            + '    repository: "repo", taskId: "task-1", sessionId: "sess-1",\n'
            + '    agent: "planner", startedAt: "2025-01-01T00:00:00Z",\n'
            + '    settledAt: "2025-01-01T00:01:00Z", contentHash: "",\n'
            + '    messages: [{ id: "u1", role: "user", text: "hello" }],\n'
            + "  };\n"
            + "  const ref = await store.writeTurn(block);\n"
            + "  const restored = await store.readTurn(ref);\n"
            + '  fs.rmSync(root, { recursive: true, force: true });\n'
            + '  console.log(JSON.stringify({ ref, id: restored.id,'
            + " repository: restored.repository, messages: restored.messages }));\n"
            + "})();\n"
        )
        data = self._run_bun_test(code)
        self.assertIn("repo", data.get("ref", ""))
        self.assertEqual(data.get("id"), "turn-1")
        self.assertEqual(data.get("repository"), "repo")
        messages = data.get("messages", [])
        self.assertTrue(len(messages) > 0)
        self.assertEqual(messages[0].get("text"), "hello")

    def test_read_by_offset_descending(self):
        code = (
            'import { FileColdStore } from "'
            + FILE_COLD_STORE_ABS
            + "\";\n"
            + 'import * as os from "node:os";\n'
            + 'import * as path from "node:path";\n'
            + 'import * as fs from "node:fs";\n'
            + "(async () => {\n"
            + '  const root = fs.mkdtempSync(path.join(os.tmpdir(), "cold-"));\n'
            + "  const store = new FileColdStore(root);\n"
            + '  const scope = { repository: "repo", taskId: "task-1",'
            + ' sessionId: "sess-1", agent: "planner" };\n'
            + "  for (let i = 1; i <= 3; i++) {\n"
            + "    const block = {\n"
            + '      version: 1, id: "turn-" + i, sequence: i,'
            + ' previous: i > 1 ? "turn-" + (i - 1) : null,\n'
            + '      repository: "repo", taskId: "task-1", sessionId: "sess-1",\n'
            + '      agent: "planner", startedAt: "2025-01-01T00:00:0" + i + "Z",\n'
            + '      settledAt: "2025-01-01T00:01:0" + i + "Z", contentHash: "",\n'
            + "      messages: [],\n"
            + "    };\n"
            + "    await store.writeTurn(block);\n"
            + "  }\n"
            + "  const desc = await store.readByOffset(scope, 0, 3);\n"
            + "  const seqs = desc.map(t => t.sequence);\n"
            + "  const one = await store.readByOffset(scope, 1, 1);\n"
            + "  const oneSeq = one.map(t => t.sequence);\n"
            + '  fs.rmSync(root, { recursive: true, force: true });\n'
            + '  console.log(JSON.stringify({ seqs, oneSeq }));\n'
            + "})();\n"
        )
        data = self._run_bun_test(code)
        self.assertEqual(data.get("seqs"), [3, 2, 1])
        self.assertEqual(data.get("oneSeq"), [2])

    def test_hash_conflict_throws(self):
        code = (
            'import { FileColdStore } from "'
            + FILE_COLD_STORE_ABS
            + "\";\n"
            + 'import * as os from "node:os";\n'
            + 'import * as path from "node:path";\n'
            + 'import * as fs from "node:fs";\n'
            + "(async () => {\n"
            + '  const root = fs.mkdtempSync(path.join(os.tmpdir(), "cold-"));\n'
            + "  const store = new FileColdStore(root);\n"
            + "  const block1 = {\n"
            + '    version: 1, id: "turn-1", sequence: 1, previous: null,\n'
            + '    repository: "repo", taskId: "task-1", sessionId: "sess-1",\n'
            + '    agent: "planner", startedAt: "2025-01-01T00:00:00Z",\n'
            + '    settledAt: "2025-01-01T00:01:00Z", contentHash: "",\n'
            + '    messages: [{ id: "u1", role: "user", text: "hello" }],\n'
            + "  };\n"
            + "  await store.writeTurn(block1);\n"
            + "  const block2 = {\n"
            + '    version: 1, id: "turn-1", sequence: 1, previous: null,\n'
            + '    repository: "repo", taskId: "task-1", sessionId: "sess-1",\n'
            + '    agent: "planner", startedAt: "2025-01-01T00:00:00Z",\n'
            + '    settledAt: "2025-01-01T00:01:00Z", contentHash: "",\n'
            + '    messages: [{ id: "u1", role: "user", text: "different" }],\n'
            + "  };\n"
            + '  let threw = false;\n'
            + '  let errMsg = "";\n'
            + "  try {\n"
            + "    await store.writeTurn(block2);\n"
            + "  } catch (e) {\n"
            + "    threw = true;\n"
            + '    errMsg = e instanceof Error ? e.message : String(e);\n'
            + "  }\n"
            + '  fs.rmSync(root, { recursive: true, force: true });\n'
            + '  console.log(JSON.stringify({ threw, errMsg }));\n'
            + "})();\n"
        )
        data = self._run_bun_test(code)
        self.assertTrue(
            data.get("threw"),
            "writing different content to same path must throw",
        )

    def test_idempotent_same_hash(self):
        code = (
            'import { FileColdStore } from "'
            + FILE_COLD_STORE_ABS
            + "\";\n"
            + 'import * as os from "node:os";\n'
            + 'import * as path from "node:path";\n'
            + 'import * as fs from "node:fs";\n'
            + "(async () => {\n"
            + '  const root = fs.mkdtempSync(path.join(os.tmpdir(), "cold-"));\n'
            + "  const store = new FileColdStore(root);\n"
            + "  const block = {\n"
            + '    version: 1, id: "turn-1", sequence: 1, previous: null,\n'
            + '    repository: "repo", taskId: "task-1", sessionId: "sess-1",\n'
            + '    agent: "planner", startedAt: "2025-01-01T00:00:00Z",\n'
            + '    settledAt: "2025-01-01T00:01:00Z", contentHash: "",\n'
            + '    messages: [{ id: "u1", role: "user", text: "hello" }],\n'
            + "  };\n"
            + "  const ref1 = await store.writeTurn(block);\n"
            + '  let threw = false;\n'
            + '  let ref2 = "";\n'
            + "  try {\n"
            + "    ref2 = await store.writeTurn(block);\n"
            + "  } catch (e) {\n"
            + "    threw = true;\n"
            + "  }\n"
            + '  fs.rmSync(root, { recursive: true, force: true });\n'
            + '  console.log(JSON.stringify({ threw, same: ref1 === ref2 }));\n'
            + "})();\n"
        )
        data = self._run_bun_test(code)
        self.assertFalse(
            data.get("threw"),
            "writing same content twice must not throw",
        )
        self.assertTrue(
            data.get("same"),
            "second write must return same ref (idempotent)",
        )

    def test_writes_to_cold_store_not_knowledge(self):
        code = (
            'import { FileColdStore } from "'
            + FILE_COLD_STORE_ABS
            + "\";\n"
            + 'import * as os from "node:os";\n'
            + 'import * as path from "node:path";\n'
            + 'import * as fs from "node:fs";\n'
            + "(async () => {\n"
            + '  const root = fs.mkdtempSync(path.join(os.tmpdir(), "cold-"));\n'
            + "  const store = new FileColdStore(root);\n"
            + "  const block = {\n"
            + '    version: 1, id: "turn-1", sequence: 1, previous: null,\n'
            + '    repository: "repo", taskId: "task-1", sessionId: "sess-1",\n'
            + '    agent: "planner", startedAt: "2025-01-01T00:00:00Z",\n'
            + '    settledAt: "2025-01-01T00:01:00Z", contentHash: "",\n'
            + '    messages: [{ id: "u1", role: "user", text: "hello" }],\n'
            + "  };\n"
            + "  const ref = await store.writeTurn(block);\n"
            + '  fs.rmSync(root, { recursive: true, force: true });\n'
            + '  console.log(JSON.stringify({ ref }));\n'
            + "})();\n"
        )
        data = self._run_bun_test(code)
        ref = data.get("ref", "")
        self.assertNotIn("knowledge", ref)


# --------------------------------------------------------------------
# MigrationScriptTests
# --------------------------------------------------------------------


class MigrationScriptTests(unittest.TestCase):
    """The one-off cold-store migration is retired.

    The defect it repaired (raw XML turns written under knowledge/ by the
    pre-rename adapter) is fixed in file-cold-store, which writes only to
    state/cold-store/. The migration script is therefore removed rather
    than kept as permanent surface. The invariant it protected is still
    asserted by ColdStoreRootTests.test_default_root_not_in_knowledge and
    FileColdStoreBehavioralTests.test_writes_to_cold_store_not_knowledge
    in this module.
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
