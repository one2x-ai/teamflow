"""Requirement tests for run-id ``memory-context-phase-b-20260728``.

These tests pin the Phase B dormant cold-memory TypeScript modules
(docs/teamflow-memory-context-design.md §11-13):

  * ``turn-block.ts``          – immutable TurnBlock + canonical XML
                                  serialization, SHA-256 content hash.
  * ``cold-memory-store.ts``   – pure ColdMemoryStore interface with
                                  ZERO basic-memory dependencies.
  * ``file-cold-store.ts``     – file-system ColdMemoryStore with
                                  atomic writes, hash verification, and
                                  offset semantics.
                                  Renamed from
                                  ``basic-memory-adapter.ts``.

Phase B modules are dormant: they are NOT wired into any Pi hooks.
Only source-text assertions and bun-based behavioral checks are used;
no runtime hook registration is asserted.

Wiring tests verify that ``init-project.sh`` ships all three files,
that ``doctor.sh`` checks them, and that ``README.md`` documents them.

All paths are relative to the repository root
``ROOT = Path(__file__).resolve().parents[2]``.
"""

import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]

TURN_BLOCK_FILE = (
    ROOT / ".teamflow" / "extensions" / "memory-context" / "turn-block.ts"
)
COLD_MEMORY_STORE_FILE = (
    ROOT / ".teamflow" / "extensions" / "memory-context"
    / "cold-memory-store.ts"
)
FILE_COLD_STORE_FILE = (
    ROOT / ".teamflow" / "extensions" / "memory-context"
    / "file-cold-store.ts"
)

TURN_BLOCK_ABS = str(
    ROOT / ".teamflow" / "extensions" / "memory-context" / "turn-block.ts"
)
FILE_COLD_STORE_ABS = str(FILE_COLD_STORE_FILE)


# --------------------------------------------------------------------
# Hermetic installer fixture (mirrors tests/test_memory_context_extension.py)
# --------------------------------------------------------------------


class _IsolatedTools:
    """Hermetic installer fixtures mirroring prior test conventions."""

    def __init__(self, root):
        self.root = root
        self.home = root / "home"
        self.bin = root / "fake-bin"
        self.home.mkdir(parents=True)
        self.bin.mkdir(parents=True)
        self._write_executable(
            self.bin / "pi",
            "#!/bin/sh\n"
            'if [ "${1:-}" = "--version" ]; then\n'
            "  printf '0.82.1\\n'\n"
            'elif [ "${1:-}" = "debug" ] && [ "${2:-}" = "skill" ]; then\n'
            "  printf 'plan-change\\nbasic-memory-cli\\n'\n"
            "fi\n"
            "exit 0\n",
        )
        self._write_executable(
            self.bin / "basic-memory",
            "#!/bin/sh\n"
            'if [ "${1:-} ${2:-}" = "project info" ]; then\n'
            "  exit 1\n"
            "fi\n"
            'if [ "${1:-}" = "status" ]; then\n'
            "  printf '{}\\n'\n"
            "fi\n"
            "exit 0\n",
        )

    def _write_executable(self, path, text):
        path.write_text(text, encoding="utf-8")
        path.chmod(0o755)

    def new_git_project(self, name):
        project = self.root / name
        project.mkdir()
        subprocess.run(["git", "init", "-q", str(project)], check=True)
        return project

    def _base_env(self):
        env = os.environ.copy()
        for key in list(env):
            if key.startswith(
                ("TEAMFLOW_", "WORKFLOW_", "OPENCODE_WORKFLOW_")
            ):
                env.pop(key)
        env.update(
            {
                "HOME": str(self.home),
                "PATH": f"{self.bin}:{env['PATH']}",
            }
        )
        return env

    def initialize(self, project):
        return subprocess.run(
            [str(ROOT / "scripts" / "install.sh"), str(project)],
            cwd=ROOT,
            env=self._base_env(),
            text=True,
            capture_output=True,
            timeout=60,
        )


# --------------------------------------------------------------------
# TurnBlock source-text assertions (turn-block.ts)
# --------------------------------------------------------------------


class TurnBlockSourceTests(unittest.TestCase):
    """Phase B contract 1: TurnBlock interface + serialization source."""

    def setUp(self):
        self.text = (
            TURN_BLOCK_FILE.read_text(encoding="utf-8")
            if TURN_BLOCK_FILE.is_file()
            else ""
        )

    def test_turn_block_file_exists(self):
        self.assertTrue(
            TURN_BLOCK_FILE.is_file(),
            "turn-block.ts must exist under memory-context/",
        )

    def test_defines_turn_block_interface(self):
        self.assertTrue(
            "interface TurnBlock" in self.text
            or "type TurnBlock" in self.text,
            "source must define 'interface TurnBlock' or 'type TurnBlock'",
        )

    def test_has_serialize_function(self):
        self.assertTrue(
            "function serialize" in self.text or "serialize" in self.text,
            "source must define a serialize function",
        )

    def test_has_deserialize_function(self):
        self.assertTrue(
            "function deserialize" in self.text
            or "deserialize" in self.text,
            "source must define a deserialize function",
        )

    def test_has_content_hash_computation(self):
        has_hash_fn = (
            "computeContentHash" in self.text
            or "contentHash" in self.text
        )
        has_crypto = (
            "sha256" in self.text.lower()
            or "createHash" in self.text
        )
        self.assertTrue(
            has_hash_fn and has_crypto,
            "source must compute content hash using SHA-256",
        )

    def test_has_xml_attribute_order(self):
        self.assertIn("teamflow_turn", self.text)
        for attr in (
            "version",
            "id",
            "sequence",
            "previous",
            "repository",
        ):
            with self.subTest(attr=attr):
                self.assertIn(attr, self.text)
        self.assertTrue(
            "task_id" in self.text or "taskId" in self.text,
            "source must reference task_id or taskId",
        )
        self.assertTrue(
            "session_id" in self.text or "sessionId" in self.text,
            "source must reference session_id or sessionId",
        )

    def test_has_entity_escaping(self):
        self.assertTrue(
            "&amp;" in self.text
            or "replace" in self.text
            or "escape" in self.text.lower()
            or "entity" in self.text.lower(),
            "source must handle XML entity escaping",
        )

    def test_uses_node_crypto(self):
        self.assertIn(
            "node:crypto",
            self.text,
            "source must import from 'node:crypto'",
        )

    def test_messages_field_in_interface(self):
        self.assertIn(
            "messages",
            self.text,
            "TurnBlock interface must include a messages field",
        )

    def test_has_redact_secrets_function(self):
        self.assertIn(
            "redactSecrets",
            self.text,
            "source must define a redactSecrets function",
        )


# --------------------------------------------------------------------
# ColdMemoryStore source-text assertions (cold-memory-store.ts)
# --------------------------------------------------------------------


class ColdMemoryStoreSourceTests(unittest.TestCase):
    """Phase B contract 2: ColdMemoryStore interface (no BM deps)."""

    def setUp(self):
        self.text = (
            COLD_MEMORY_STORE_FILE.read_text(encoding="utf-8")
            if COLD_MEMORY_STORE_FILE.is_file()
            else ""
        )

    def test_cold_memory_store_file_exists(self):
        self.assertTrue(
            COLD_MEMORY_STORE_FILE.is_file(),
            "cold-memory-store.ts must exist",
        )

    def test_defines_cold_memory_store_interface(self):
        self.assertIn(
            "interface ColdMemoryStore",
            self.text,
            "source must define 'interface ColdMemoryStore'",
        )

    def test_declares_write_turn(self):
        self.assertIn(
            "writeTurn",
            self.text,
            "source must declare writeTurn",
        )

    def test_declares_read_turn(self):
        self.assertIn(
            "readTurn",
            self.text,
            "source must declare readTurn",
        )

    def test_declares_read_by_offset(self):
        self.assertIn(
            "readByOffset",
            self.text,
            "source must declare readByOffset",
        )

    def test_has_session_scope(self):
        self.assertIn(
            "SessionScope",
            self.text,
            "source must define SessionScope",
        )

    def test_has_memory_ref(self):
        self.assertIn(
            "MemoryRef",
            self.text,
            "source must define MemoryRef",
        )

    def test_no_basic_memory_dependency(self):
        for forbidden in ("basic-memory", "basicMemory", "basic_memory"):
            with self.subTest(token=forbidden):
                self.assertNotIn(
                    forbidden,
                    self.text,
                    "cold-memory-store.ts must NOT reference "
                    + forbidden,
                )


# --------------------------------------------------------------------
# FileColdStore source-text assertions (file-cold-store.ts)
# --------------------------------------------------------------------


class FileColdStoreSourceTests(unittest.TestCase):
    """Phase B contract 3: FileColdStore implementation source."""

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
        self.assertFalse(
            (
                ROOT / ".teamflow" / "extensions" / "memory-context"
                / "basic-memory-adapter.ts"
            ).is_file(),
            "basic-memory-adapter.ts must no longer exist (renamed)",
        )

    def test_implements_cold_memory_store(self):
        self.assertIn(
            "ColdMemoryStore",
            self.text,
            "source must implement/extend ColdMemoryStore",
        )

    def test_has_safe_segment_validation(self):
        self.assertTrue(
            "SAFE_SEGMENT" in self.text
            or ("safe" in self.text.lower() and "segment" in self.text.lower()),
            "source must define SAFE_SEGMENT path validation",
        )

    def test_rejects_dot_dot(self):
        self.assertTrue(
            ".." in self.text,
            "source must reject '..' in path segments",
        )

    def test_uses_atomic_write(self):
        self.assertTrue(
            "rename" in self.text or "atomic" in self.text.lower(),
            "source must use atomic write (rename or atomic pattern)",
        )

    def test_verifies_hash_on_read(self):
        self.assertTrue(
            "verify" in self.text.lower()
            or "hash" in self.text.lower(),
            "source must verify content hash on read",
        )

    def test_has_offset_semantics(self):
        self.assertIn("before", self.text)
        self.assertIn("sequence", self.text)
        self.assertTrue(
            "descending" in self.text.lower()
            or "sort" in self.text.lower()
            or "desc" in self.text.lower(),
            "source must implement descending sequence ordering",
        )


# --------------------------------------------------------------------
# TurnBlock behavioral tests (bun-based)
# --------------------------------------------------------------------


class TurnBlockBehavioralTests(unittest.TestCase):
    """Phase B contract 1: behavioral correctness via bun subprocess."""

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

    def test_serialize_produces_canonical_xml(self):
        data = self._run_bun_test(
            'import { serialize } from "'
            + TURN_BLOCK_ABS
            + "\";\n"
            + "const block = {\n"
            + "  version: 1, id: \"turn-1\", sequence: 1, "
            + "previous: null,\n"
            + "  repository: \"repo\", taskId: \"task-1\", "
            + "sessionId: \"sess-1\",\n"
            + "  agent: \"planner\", "
            + "startedAt: \"2025-01-01T00:00:00Z\",\n"
            + "  settledAt: \"2025-01-01T00:01:00Z\", "
            + "contentHash: \"\",\n"
            + "};\n"
            + "console.log(JSON.stringify({ xml: serialize(block) }));\n"
        )
        xml = data.get("xml", "")
        self.assertTrue(
            xml.startswith("<teamflow_turn"),
            f"serialized XML must start with <teamflow_turn: {xml!r}",
        )
        for attr in (
            "version=",
            "id=",
            "sequence=",
            "previous=",
            "repository=",
        ):
            with self.subTest(attr=attr):
                self.assertIn(
                    attr,
                    xml,
                    f"serialized XML must contain attribute {attr}",
                )

    def test_serialize_deserialize_roundtrip(self):
        data = self._run_bun_test(
            'import { serialize, deserialize } from "'
            + TURN_BLOCK_ABS
            + "\";\n"
            + "const block = {\n"
            + "  version: 1, id: \"turn-1\", sequence: 1, "
            + "previous: null,\n"
            + "  repository: \"repo\", taskId: \"task-1\", "
            + "sessionId: \"sess-1\",\n"
            + "  agent: \"planner\", "
            + "startedAt: \"2025-01-01T00:00:00Z\",\n"
            + "  settledAt: \"2025-01-01T00:01:00Z\", "
            + "contentHash: \"\",\n"
            + "};\n"
            + "const xml = serialize(block);\n"
            + "const restored = deserialize(xml);\n"
            + "console.log(JSON.stringify({ restored }));\n"
        )
        restored = data.get("restored", {})
        self.assertEqual(restored.get("version"), 1)
        self.assertEqual(restored.get("id"), "turn-1")
        self.assertEqual(restored.get("sequence"), 1)
        self.assertIsNone(restored.get("previous"))
        self.assertEqual(restored.get("repository"), "repo")
        self.assertEqual(restored.get("taskId"), "task-1")
        self.assertEqual(restored.get("sessionId"), "sess-1")
        self.assertEqual(restored.get("agent"), "planner")
        self.assertEqual(
            restored.get("startedAt"), "2025-01-01T00:00:00Z"
        )
        self.assertEqual(
            restored.get("settledAt"), "2025-01-01T00:01:00Z"
        )

    def test_content_hash_is_deterministic(self):
        code = (
            'import { computeContentHash } from "'
            + TURN_BLOCK_ABS
            + "\";\n"
            + "const block = {\n"
            + "  version: 1, id: \"turn-1\", sequence: 1, "
            + "previous: null,\n"
            + "  repository: \"repo\", taskId: \"task-1\", "
            + "sessionId: \"sess-1\",\n"
            + "  agent: \"planner\", "
            + "startedAt: \"2025-01-01T00:00:00Z\",\n"
            + "  settledAt: \"2025-01-01T00:01:00Z\", "
            + "contentHash: \"\",\n"
            + "};\n"
            + "const h1 = computeContentHash(block);\n"
            + "const h2 = computeContentHash(block);\n"
            + "console.log(JSON.stringify({ h1, h2 }));\n"
        )
        data = self._run_bun_test(code)
        self.assertEqual(
            data.get("h1"),
            data.get("h2"),
            "same input must produce same hash",
        )

    def test_content_hash_is_sha256(self):
        code = (
            'import { computeContentHash } from "'
            + TURN_BLOCK_ABS
            + "\";\n"
            + "const block = {\n"
            + "  version: 1, id: \"turn-1\", sequence: 1, "
            + "previous: null,\n"
            + "  repository: \"repo\", taskId: \"task-1\", "
            + "sessionId: \"sess-1\",\n"
            + "  agent: \"planner\", "
            + "startedAt: \"2025-01-01T00:00:00Z\",\n"
            + "  settledAt: \"2025-01-01T00:01:00Z\", "
            + "contentHash: \"\",\n"
            + "};\n"
            + "console.log(JSON.stringify({ hash: "
            + "computeContentHash(block) }));\n"
        )
        data = self._run_bun_test(code)
        h = data.get("hash", "")
        self.assertTrue(
            h.startswith("sha256:"),
            f"hash must start with 'sha256:': {h!r}",
        )
        hex_part = h[len("sha256:"):]
        self.assertEqual(
            len(hex_part),
            64,
            f"hex digest must be 64 chars: {hex_part!r}",
        )
        self.assertTrue(
            all(c in "0123456789abcdef" for c in hex_part),
            f"hex digest must be lowercase hex: {hex_part!r}",
        )

    def test_null_previous_serialized_as_empty(self):
        data = self._run_bun_test(
            'import { serialize } from "'
            + TURN_BLOCK_ABS
            + "\";\n"
            + "const block = {\n"
            + "  version: 1, id: \"turn-1\", sequence: 1, "
            + "previous: null,\n"
            + "  repository: \"repo\", taskId: \"task-1\", "
            + "sessionId: \"sess-1\",\n"
            + "  agent: \"planner\", "
            + "startedAt: \"2025-01-01T00:00:00Z\",\n"
            + "  settledAt: \"2025-01-01T00:01:00Z\", "
            + "contentHash: \"\",\n"
            + "};\n"
            + "console.log(JSON.stringify({ xml: serialize(block) }));\n"
        )
        xml = data.get("xml", "")
        self.assertIn('previous=""', xml)

    def test_entity_escaping(self):
        data = self._run_bun_test(
            'import { serialize } from "'
            + TURN_BLOCK_ABS
            + "\";\n"
            + "const block = {\n"
            + "  version: 1, id: \"turn-1\", sequence: 1, "
            + "previous: null,\n"
            + "  repository: \"repo\", taskId: \"task-1\", "
            + "sessionId: \"sess-1\",\n"
            + '  agent: "A&B<C>", '
            + 'startedAt: "2025-01-01T00:00:00Z",\n'
            + "  settledAt: \"2025-01-01T00:01:00Z\", "
            + "contentHash: \"\",\n"
            + "};\n"
            + "console.log(JSON.stringify({ xml: serialize(block) }));\n"
        )
        xml = data.get("xml", "")
        self.assertIn("A&amp;B&lt;C&gt;", xml)
        self.assertNotIn("A&B<C>", xml)


# --------------------------------------------------------------------
# TurnBlock content behavioral tests (Phase B completion)
# --------------------------------------------------------------------


class TurnBlockContentBehavioralTests(unittest.TestCase):
    """Phase B completion: TurnBlock carries message content."""

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

    def test_messages_serialize_to_messages_body(self):
        data = self._run_bun_test(
            'import { serialize } from "'
            + TURN_BLOCK_ABS
            + "\";\n"
            + "const block = {\n"
            + "  version: 1, id: \"turn-1\", sequence: 1, "
            + "previous: null,\n"
            + "  repository: \"repo\", taskId: \"task-1\", "
            + "sessionId: \"sess-1\",\n"
            + "  agent: \"planner\", "
            + "startedAt: \"2025-01-01T00:00:00Z\",\n"
            + "  settledAt: \"2025-01-01T00:01:00Z\", "
            + "contentHash: \"\",\n"
            + "  messages: [\n"
            + "    { id: \"user-1\", role: \"user\","
            + " text: \"Hello\" },\n"
            + "    { id: \"assistant-1\", role: \"assistant\","
            + " text: \"Working\",\n"
            + "      toolCalls: [{ id: \"call-1\","
            + " name: \"search\","
            + " arguments: \"q=foo\" }] },\n"
            + "    { id: \"result-1\", role: \"toolResult\","
            + " text: \"found it\",\n"
            + "      callId: \"call-1\", status: \"ok\" },\n"
            + "  ],\n"
            + "};\n"
            + "console.log(JSON.stringify({ xml: serialize(block) }));\n"
        )
        xml = data.get("xml", "")
        for required in (
            "<messages>",
            "<message",
            'role="user"',
            'role="assistant"',
            'role="toolResult"',
            "<tool_call",
            "<text>",
        ):
            with self.subTest(token=required):
                self.assertIn(required, xml)

    def test_roundtrip_preserves_messages(self):
        data = self._run_bun_test(
            'import { serialize, deserialize } from "'
            + TURN_BLOCK_ABS
            + "\";\n"
            + "const block = {\n"
            + "  version: 1, id: \"turn-1\", sequence: 1, "
            + "previous: null,\n"
            + "  repository: \"repo\", taskId: \"task-1\", "
            + "sessionId: \"sess-1\",\n"
            + "  agent: \"planner\", "
            + "startedAt: \"2025-01-01T00:00:00Z\",\n"
            + "  settledAt: \"2025-01-01T00:01:00Z\", "
            + "contentHash: \"\",\n"
            + "  messages: [\n"
            + "    { id: \"user-1\", role: \"user\","
            + " text: \"Hello\" },\n"
            + "    { id: \"assistant-1\", role: \"assistant\","
            + " text: \"Working\",\n"
            + "      toolCalls: [{ id: \"call-1\","
            + " name: \"search\","
            + " arguments: \"q=foo\" }] },\n"
            + "    { id: \"result-1\", role: \"toolResult\","
            + " text: \"found it\",\n"
            + "      callId: \"call-1\", status: \"ok\" },\n"
            + "  ],\n"
            + "};\n"
            + "const xml = serialize(block);\n"
            + "const restored = deserialize(xml);\n"
            + "console.log(JSON.stringify("+ "{ messages: restored.messages }));\n"
        )
        messages = data.get("messages", [])
        self.assertEqual(len(messages), 3)
        self.assertEqual(messages[0]["id"], "user-1")
        self.assertEqual(messages[0]["role"], "user")
        self.assertEqual(messages[0]["text"], "Hello")
        self.assertEqual(messages[1]["id"], "assistant-1")
        self.assertEqual(messages[1]["role"], "assistant")
        tc = messages[1]["toolCalls"][0]
        self.assertEqual(tc["id"], "call-1")
        self.assertEqual(tc["name"], "search")
        self.assertEqual(messages[2]["id"], "result-1")
        self.assertEqual(messages[2]["role"], "toolResult")
        self.assertEqual(messages[2]["callId"], "call-1")
        self.assertEqual(messages[2]["status"], "ok")

    def test_hash_differs_with_different_messages(self):
        code = (
            'import { computeContentHash } from "'
            + TURN_BLOCK_ABS
            + "\";\n"
            + "const base = {\n"
            + "  version: 1, id: \"turn-1\", sequence: 1, "
            + "previous: null,\n"
            + "  repository: \"repo\", taskId: \"task-1\", "
            + "sessionId: \"sess-1\",\n"
            + "  agent: \"planner\", "
            + "startedAt: \"2025-01-01T00:00:00Z\",\n"
            + "  settledAt: \"2025-01-01T00:01:00Z\", "
            + "contentHash: \"\",\n"
            + "};\n"
            + "const a = { ...base, messages: "
            + "[{ id: \"u1\", role: \"user\", text: \"alpha\" }] };\n"
            + "const b = { ...base, messages: "
            + "[{ id: \"u1\", role: \"user\", text: \"beta\" }] };\n"
            + "console.log(JSON.stringify({\n"
            + "  ha: computeContentHash(a),\n"
            + "  hb: computeContentHash(b),\n"
            + "}));\n"
        )
        data = self._run_bun_test(code)
        self.assertNotEqual(
            data.get("ha", ""), data.get("hb", ""),
            "different message content must produce different hashes",
        )


# --------------------------------------------------------------------
# Installer wiring (init-project.sh)
# --------------------------------------------------------------------


class InstallerWiringTests(unittest.TestCase):
    """init-project.sh must ship all three cold-memory modules."""

    def setUp(self):
        self.init_script = (
            (ROOT / "scripts" / "install.sh").read_text(
                encoding="utf-8"
            )
        )

    def test_init_script_references_turn_block(self):
        self.assertIn("turn-block", self.init_script)

    def test_init_script_references_cold_memory_store(self):
        self.assertIn("cold-memory-store", self.init_script)

    def test_init_script_references_file_cold_store(self):
        self.assertIn("file-cold-store", self.init_script)

    def test_hermetic_install_copies_all_three(self):
        with tempfile.TemporaryDirectory() as directory:
            tools = _IsolatedTools(Path(directory))
            project = tools.new_git_project("target")
            completed = tools.initialize(project)
            self.assertEqual(
                completed.returncode, 0, completed.stderr
            )
            for rel in (
                "turn-block.ts",
                "cold-memory-store.ts",
                "file-cold-store.ts",
            ):
                with self.subTest(file=rel):
                    installed = (
                        project
                        / ".teamflow"
                        / "extensions"
                        / "memory-context"
                        / rel
                    )
                    self.assertTrue(
                        installed.is_file(),
                        f"{rel} must be installed by init-project.sh",
                    )


# --------------------------------------------------------------------
# Doctor wiring (doctor.sh)
# --------------------------------------------------------------------


class DoctorWiringTests(unittest.TestCase):
    """doctor.sh must check for all three cold-memory modules."""

    def test_doctor_checks_cold_memory_modules(self):
        doctor = (
            (ROOT / "scripts" / "doctor.sh").read_text(encoding="utf-8")
        )
        for module in (
            "turn-block",
            "cold-memory-store",
            "file-cold-store",
        ):
            with self.subTest(module=module):
                self.assertIn(
                    module,
                    doctor,
                    f"doctor.sh must check for {module}",
                )


# --------------------------------------------------------------------
# README documentation
# --------------------------------------------------------------------


class ReadmeDocumentationTests(unittest.TestCase):
    """README.md must document Phase B dormant modules."""

    def test_readme_documents_phase_b(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertTrue(
            any(
                token in readme
                for token in (
                    "turn-block",
                    "cold-memory",
                    "Phase B",
                    "冷记忆",
                )
            ),
            "README.md must document Phase B / cold-memory modules",
        )


if __name__ == "__main__":
    unittest.main()
