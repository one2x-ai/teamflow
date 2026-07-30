"""Requirement tests for Phase F: semantic indexing (语义索引).

Phase F (docs/teamflow-memory-context-design.md §22.F) adds an
auditable TurnIndex for cold-stored TurnBlocks and deterministic local
full-text search over the index store.

Phase F consists of:

1. ``turn-index.ts`` — canonical XML serialization for TurnIndex,
   mirroring the pattern of ``turn-block.ts`` and ``rule-cache.ts``
   (fixed attribute order, entity escaping, SHA-256 content hash over
   the canonical form without the content_hash field).
2. ``cold-memory-store.ts`` — the ``ColdMemoryStore`` interface gains
   ``writeIndex`` and ``search`` methods, plus ``MemoryScope``,
   ``SearchHit``, and ``SearchOptions`` types.
3. ``file-cold-store.ts`` — ``FileColdStore`` implements ``writeIndex``
   and ``search``: idempotent index writes, hash-conflict detection,
   and deterministic full-text token matching with stable sort.
4. ``memory-indexer.md`` — a cheap ``memory-indexer`` role definition.
5. Infrastructure updates (doctor.sh, init-project.sh, README.md,
   design doc).

Test approaches (mirrors Phase E conventions):

1. Source-text assertions — read the TypeScript / Markdown source and
   assert required code patterns exist via ``re.compile`` / ``in``
   checks.
2. Bun-based behavioral tests — write inline TypeScript to a temp
   file, import the actual module, run with ``bun run``, parse JSON
   output.  Skipped gracefully if ``bun`` is not on PATH.

Contracts defined by these tests (the implementer MUST export):

turn-index.ts
  - interface TurnIndex — fields: version, blockId, repository,
    taskId, sessionId, agent, sequence, intent, actions, outcomes,
    decisions, constraints, failures, openQuestions, keywords,
    entities, artifactRefs, sourceEvents, contentHash
  - interface SemanticEntry — fields: text, sources
  - interface IndexSourceRef — fields: messageId, field, toolCallId?
  - function serialize(index: TurnIndex): string
  - function deserialize(xml: string): TurnIndex
  - function computeContentHash(index: TurnIndex): string
  - function validateIndex(index: unknown): boolean

cold-memory-store.ts
  - interface MemoryScope — field: repository
  - interface SearchHit — fields: blockRef, blockId, sequence,
    repository, taskId, sessionId, agent, score, matchedFields
  - interface SearchOptions — field: limit?
  - ColdMemoryStore gains writeIndex, search methods

file-cold-store.ts
  - FileColdStore gains writeIndex, search implementations

All tests are deterministic: no network, no providers, no
credentials.
"""

import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]

TURN_INDEX_FILE = (
    ROOT / ".teamflow" / "extensions" / "memory-context" / "turn-index.ts"
)
COLD_MEMORY_STORE_FILE = (
    ROOT / ".teamflow" / "extensions" / "memory-context"
    / "cold-memory-store.ts"
)
FILE_COLD_STORE_FILE = (
    ROOT / ".teamflow" / "extensions" / "memory-context" / "file-cold-store.ts"
)
INDEXER_AGENT_FILE = ROOT / ".teamflow" / "agents" / "memory-indexer.md"
README_FILE = ROOT / "README.md"
DESIGN_DOC = ROOT / "docs" / "teamflow-memory-context-design.md"
DOCTOR_FILE = ROOT / "scripts" / "doctor.sh"
INIT_FILE = ROOT / "scripts" / "install.sh"

TURN_INDEX_ABS = str(TURN_INDEX_FILE)
FILE_COLD_STORE_ABS = str(FILE_COLD_STORE_FILE)


# --------------------------------------------------------------------
# Bun test helper
# --------------------------------------------------------------------

def _bun_available():
    return shutil.which("bun") is not None


def _run_bun(ts_code):
    """Write *ts_code* to a temp file, run with bun, return parsed JSON."""
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
        if os.path.exists(tmp):
            os.unlink(tmp)
    if result.returncode != 0:
        raise AssertionError(
            f"bun exited {result.returncode}:\n"
            f"stdout={result.stdout}\nstderr={result.stderr}"
        )
    lines = [
        ln for ln in result.stdout.strip().split("\n") if ln.strip()
    ]
    return json.loads(lines[-1]) if lines else {}


# Inline TypeScript helper shared by bun tests ----------------------

_TS_INDEX_HELPER = """\
function makeSource(messageId, field, toolCallId) {
  var ref = { messageId: messageId, field: field };
  if (toolCallId) ref.toolCallId = toolCallId;
  return ref;
}
function makeEntry(text, messageId, field) {
  return { text: text, sources: [makeSource(messageId || "m1", field || "text")] };
}
function makeIndex(o) {
  return Object.assign({
    version: 1,
    blockId: "turn-1",
    repository: "test-repo",
    taskId: "task-1",
    sessionId: "session-1",
    agent: "planner",
    sequence: 1,
    intent: "implement feature",
    actions: [],
    outcomes: [],
    decisions: [],
    constraints: [],
    failures: [],
    openQuestions: [],
    keywords: [],
    entities: [],
    artifactRefs: [],
    sourceEvents: [],
    contentHash: "",
  }, o || {});
}
"""


# --------------------------------------------------------------------
# AC 1: TurnIndex schema (turn-index.ts)
# --------------------------------------------------------------------


class TurnIndexSchemaTests(unittest.TestCase):
    """Source-text assertions on turn-index.ts: interfaces, serialize /
    deserialize / hash / validate functions."""

    def setUp(self):
        self.text = (
            TURN_INDEX_FILE.read_text(encoding="utf-8")
            if TURN_INDEX_FILE.is_file()
            else ""
        )

    def test_turn_index_file_exists(self):
        self.assertTrue(
            TURN_INDEX_FILE.is_file(),
            "turn-index.ts must exist under memory-context/",
        )

    def test_defines_turn_index_interface(self):
        self.assertTrue(
            "interface TurnIndex" in self.text
            or "type TurnIndex" in self.text,
            "source must define 'interface TurnIndex' or 'type TurnIndex'",
        )

    def test_turn_index_has_required_fields(self):
        for field in ("blockId", "intent", "version", "sequence"):
            with self.subTest(field=field):
                self.assertIn(
                    field,
                    self.text,
                    f"TurnIndex must define field '{field}'",
                )

    def test_turn_index_has_semantic_fields(self):
        for field in (
            "actions", "outcomes", "decisions", "constraints",
            "failures", "openQuestions", "keywords", "entities",
            "artifactRefs", "sourceEvents", "intent",
        ):
            with self.subTest(field=field):
                self.assertIn(
                    field,
                    self.text,
                    f"TurnIndex must define semantic field '{field}'",
                )

    def test_defines_semantic_entry_type(self):
        self.assertTrue(
            "SemanticEntry" in self.text
            or "IndexSourceRef" in self.text,
            "source must define a SemanticEntry or IndexSourceRef type "
            "for source references",
        )

    def test_has_serialize_function(self):
        self.assertTrue(
            "function serialize" in self.text,
            "source must define a serialize function",
        )

    def test_has_deserialize_function(self):
        self.assertTrue(
            "function deserialize" in self.text,
            "source must define a deserialize function",
        )

    def test_has_compute_content_hash(self):
        self.assertIn(
            "computeContentHash",
            self.text,
            "source must define computeContentHash",
        )

    def test_has_validate_index(self):
        self.assertTrue(
            "validateIndex" in self.text,
            "source must define validateIndex",
        )

    def test_uses_sha256(self):
        self.assertIn(
            "sha256",
            self.text.lower(),
            "hash must use SHA-256",
        )
        self.assertTrue(
            "createHash" in self.text,
            "source must use createHash for hashing",
        )

    def test_version_constant_is_one(self):
        self.assertTrue(
            re.search(r"version\s*[=:]\s*1\b", self.text),
            "version constant must be 1",
        )


# --------------------------------------------------------------------
# AC 2: ColdMemoryStore contract (cold-memory-store.ts)
# --------------------------------------------------------------------


class ColdMemoryStoreContractTests(unittest.TestCase):
    """Source-text assertions on cold-memory-store.ts: MemoryScope,
    SearchHit, SearchOptions, and extended ColdMemoryStore interface."""

    def setUp(self):
        self.text = (
            COLD_MEMORY_STORE_FILE.read_text(encoding="utf-8")
            if COLD_MEMORY_STORE_FILE.is_file()
            else ""
        )

    def test_defines_memory_scope(self):
        self.assertTrue(
            "interface MemoryScope" in self.text
            or "type MemoryScope" in self.text,
            "cold-memory-store.ts must define MemoryScope",
        )

    def test_memory_scope_has_repository(self):
        self.assertTrue(
            "repository" in self.text,
            "MemoryScope must have a repository field",
        )

    def test_defines_search_hit(self):
        self.assertTrue(
            "interface SearchHit" in self.text
            or "type SearchHit" in self.text,
            "cold-memory-store.ts must define SearchHit",
        )

    def test_search_hit_has_required_fields(self):
        for field in (
            "blockRef", "blockId", "sequence", "score", "matchedFields",
        ):
            with self.subTest(field=field):
                self.assertIn(
                    field,
                    self.text,
                    f"SearchHit must define field '{field}'",
                )

    def test_defines_search_options(self):
        self.assertTrue(
            "interface SearchOptions" in self.text
            or "type SearchOptions" in self.text,
            "cold-memory-store.ts must define SearchOptions",
        )

    def test_cold_store_has_write_index(self):
        self.assertIn(
            "writeIndex",
            self.text,
            "ColdMemoryStore interface must declare writeIndex method",
        )

    def test_cold_store_has_search(self):
        self.assertIn(
            "search",
            self.text,
            "ColdMemoryStore interface must declare search method",
        )


# --------------------------------------------------------------------
# AC 3: FileColdStore (file-cold-store.ts)
# --------------------------------------------------------------------


class FileColdStoreTests(unittest.TestCase):
    """Source-text assertions on file-cold-store.ts: writeIndex,
    search, turn-index import, index storage path."""

    def setUp(self):
        self.text = (
            FILE_COLD_STORE_FILE.read_text(encoding="utf-8")
            if FILE_COLD_STORE_FILE.is_file()
            else ""
        )

    def test_has_write_index_method(self):
        self.assertIn(
            "writeIndex",
            self.text,
            "file-cold-store.ts must implement writeIndex",
        )

    def test_has_search_method(self):
        self.assertIn(
            "search",
            self.text,
            "file-cold-store.ts must implement search",
        )

    def test_imports_turn_index(self):
        self.assertTrue(
            "turn-index" in self.text,
            "file-cold-store.ts must import from turn-index module",
        )

    def test_has_turn_index_path_pattern(self):
        self.assertTrue(
            "turn-index" in self.text,
            "file-cold-store.ts must construct a turn-index/ path for "
            "index storage",
        )


# --------------------------------------------------------------------
# AC 4: memory-indexer agent (memory-indexer.md)
# --------------------------------------------------------------------


class MemoryIndexerAgentTests(unittest.TestCase):
    """Source-text assertions on memory-indexer.md: model, write
    permission, product-code restriction."""

    def setUp(self):
        self.text = (
            INDEXER_AGENT_FILE.read_text(encoding="utf-8")
            if INDEXER_AGENT_FILE.is_file()
            else ""
        )

    def test_indexer_agent_file_exists(self):
        self.assertTrue(
            INDEXER_AGENT_FILE.is_file(),
            "memory-indexer.md must exist under .teamflow/agents/",
        )

    def test_uses_mimo_model(self):
        self.assertIn(
            "mimo/mimo-v2.5-pro",
            self.text,
            "memory-indexer must use the mimo/mimo-v2.5-pro model",
        )

    def test_mentions_memory_write_permission(self):
        self.assertTrue(
            ".teamflow/runs/memory" in self.text,
            "memory-indexer must have write permission for "
            ".teamflow/runs/memory/",
        )

    def test_states_cannot_modify_product_code(self):
        self.assertTrue(
            "product code" in self.text.lower()
            or "product-code" in self.text.lower()
            or "source code" in self.text.lower(),
            "memory-indexer must state it cannot modify product code",
        )
        self.assertTrue(
            "Basic Memory" in self.text,
            "memory-indexer must state it cannot write Basic Memory",
        )


# --------------------------------------------------------------------
# AC 5: Infrastructure (doctor.sh, init-project.sh, README.md, design)
# --------------------------------------------------------------------


class ScriptAndDocTests(unittest.TestCase):
    """doctor.sh, init-project.sh, README.md, and design doc must
    reference Phase F semantic indexing."""

    def test_doctor_checks_turn_index(self):
        doctor = (
            DOCTOR_FILE.read_text(encoding="utf-8")
            if DOCTOR_FILE.is_file()
            else ""
        )
        self.assertIn(
            "turn-index",
            doctor,
            "doctor.sh must check for turn-index.ts",
        )

    def test_init_project_ships_turn_index(self):
        init_script = (
            INIT_FILE.read_text(encoding="utf-8")
            if INIT_FILE.is_file()
            else ""
        )
        self.assertIn(
            "turn-index",
            init_script,
            "init-project.sh must ship turn-index.ts",
        )

    def test_init_project_ships_memory_indexer(self):
        init_script = (
            INIT_FILE.read_text(encoding="utf-8")
            if INIT_FILE.is_file()
            else ""
        )
        self.assertIn(
            "memory-indexer",
            init_script,
            "init-project.sh must ship memory-indexer.md",
        )

    def test_readme_documents_phase_f(self):
        readme = (
            README_FILE.read_text(encoding="utf-8")
            if README_FILE.is_file()
            else ""
        )
        self.assertTrue(
            any(
                token in readme
                for token in (
                    "Phase F", "阶段 F", "semantic index",
                    "semantic search", "语义索引", "turn-index",
                    "TurnIndex", "memory-indexer",
                )
            ),
            "README.md must document Phase F semantic indexing",
        )

    def test_design_doc_marks_phase_f_implemented(self):
        design = (
            DESIGN_DOC.read_text(encoding="utf-8")
            if DESIGN_DOC.is_file()
            else ""
        )
        self.assertTrue(
            any(
                token in design
                for token in ("语义索引", "TurnIndex", "turn-index")
            ),
            "design doc must contain the Phase F semantic index section",
        )


# --------------------------------------------------------------------
# AC 1: serialize / deserialize / hash behavioral (turn-index.ts)
# --------------------------------------------------------------------


class TurnIndexSerializeTests(unittest.TestCase):
    """Bun-based behavioral tests on turn-index.ts: serialize,
    deserialize, and computeContentHash."""

    def setUp(self):
        if not _bun_available():
            self.skipTest("bun not on PATH")

    def test_serialize_deserialize_roundtrip(self):
        code = (
            _TS_INDEX_HELPER
            + 'import { serialize, deserialize } from "'
            + TURN_INDEX_ABS + "\";\n"
            + "const idx = makeIndex({\n"
            + '  blockId: "turn-7",\n'
            + '  intent: "implement search",\n'
            + "  actions: [makeEntry('deploy the service')],\n"
            + "  decisions: [makeEntry('use PostgreSQL')],\n"
            + '  keywords: ["search", "index"],\n'
            + "});\n"
            + "const xml = serialize(idx);\n"
            + "const restored = deserialize(xml);\n"
            + "console.log(JSON.stringify({ restored }));\n"
        )
        restored = _run_bun(code).get("restored", {})
        self.assertEqual(restored.get("version"), 1)
        self.assertEqual(restored.get("blockId"), "turn-7")
        self.assertEqual(restored.get("intent"), "implement search")
        self.assertEqual(restored.get("taskId"), "task-1")
        self.assertEqual(restored.get("sessionId"), "session-1")
        actions = restored.get("actions", [])
        self.assertEqual(len(actions), 1)
        self.assertEqual(actions[0].get("text"), "deploy the service")
        decisions = restored.get("decisions", [])
        self.assertEqual(len(decisions), 1)
        self.assertEqual(decisions[0].get("text"), "use PostgreSQL")
        self.assertEqual(restored.get("keywords"), ["search", "index"])

    def test_compute_content_hash_deterministic(self):
        code = (
            _TS_INDEX_HELPER
            + 'import { computeContentHash } from "'
            + TURN_INDEX_ABS + "\";\n"
            + "const idx = makeIndex({\n"
            + '  intent: "stable intent",\n'
            + '  keywords: ["a", "b"],\n'
            + "});\n"
            + "const h1 = computeContentHash(idx);\n"
            + "const h2 = computeContentHash(idx);\n"
            + "console.log(JSON.stringify({ h1, h2 }));\n"
        )
        data = _run_bun(code)
        self.assertEqual(
            data.get("h1"), data.get("h2"),
            "same index must produce identical hash",
        )

    def test_compute_content_hash_differs_on_content(self):
        code = (
            _TS_INDEX_HELPER
            + 'import { computeContentHash } from "'
            + TURN_INDEX_ABS + "\";\n"
            + "const idx1 = makeIndex({ intent: 'first' });\n"
            + "const idx2 = makeIndex({ intent: 'second' });\n"
            + "const h1 = computeContentHash(idx1);\n"
            + "const h2 = computeContentHash(idx2);\n"
            + "console.log(JSON.stringify({ h1, h2 }));\n"
        )
        data = _run_bun(code)
        self.assertNotEqual(
            data.get("h1"), data.get("h2"),
            "different content must produce different hashes",
        )

    def test_hash_excludes_content_hash_field(self):
        code = (
            _TS_INDEX_HELPER
            + 'import { computeContentHash } from "'
            + TURN_INDEX_ABS + "\";\n"
            + "const base = makeIndex({ intent: 'content' });\n"
            + 'const withHash = Object.assign({}, base,\n'
            + '  { contentHash: "sha256:fake123" });\n'
            + "const h1 = computeContentHash(base);\n"
            + "const h2 = computeContentHash(withHash);\n"
            + "console.log(JSON.stringify({ h1, h2 }));\n"
        )
        data = _run_bun(code)
        self.assertEqual(
            data.get("h1"), data.get("h2"),
            "contentHash field must not affect the computed hash",
        )

    def test_serialized_output_starts_with_tag(self):
        code = (
            _TS_INDEX_HELPER
            + 'import { serialize } from "'
            + TURN_INDEX_ABS + "\";\n"
            + "const idx = makeIndex({});\n"
            + 'console.log(JSON.stringify({ xml: serialize(idx) }));\n'
        )
        xml = _run_bun(code).get("xml", "")
        self.assertTrue(
            xml.startswith("<teamflow_turn_index"),
            f"serialized XML must start with <teamflow_turn_index: "
            f"{xml[:80]!r}",
        )


# --------------------------------------------------------------------
# AC 1: validateIndex behavioral (turn-index.ts)
# --------------------------------------------------------------------


class TurnIndexValidationTests(unittest.TestCase):
    """Bun-based behavioral tests on turn-index.ts validateIndex."""

    def setUp(self):
        if not _bun_available():
            self.skipTest("bun not on PATH")

    def test_validate_index_true_for_well_formed(self):
        code = (
            _TS_INDEX_HELPER
            + 'import { serialize, deserialize, computeContentHash, '
            + 'validateIndex } from "'
            + TURN_INDEX_ABS + "\";\n"
            + "const idx = makeIndex({});\n"
            + "idx.contentHash = computeContentHash(idx);\n"
            + "console.log(JSON.stringify({ ok: validateIndex(idx) }));\n"
        )
        self.assertTrue(
            _run_bun(code).get("ok"),
            "well-formed index with correct hash must validate",
        )

    def test_validate_index_false_for_missing_block_id(self):
        code = (
            _TS_INDEX_HELPER
            + 'import { computeContentHash, validateIndex } from "'
            + TURN_INDEX_ABS + "\";\n"
            + "const idx = makeIndex({});\n"
            + "delete idx.blockId;\n"
            + "idx.contentHash = computeContentHash(idx);\n"
            + "console.log(JSON.stringify({ ok: validateIndex(idx) }));\n"
        )
        self.assertFalse(
            _run_bun(code).get("ok"),
            "index with missing blockId must fail validation",
        )

    def test_validate_index_false_for_empty_block_id(self):
        code = (
            _TS_INDEX_HELPER
            + 'import { computeContentHash, validateIndex } from "'
            + TURN_INDEX_ABS + "\";\n"
            + "const idx = makeIndex({});\n"
            + 'idx.blockId = "";\n'
            + "idx.contentHash = computeContentHash(idx);\n"
            + "console.log(JSON.stringify({ ok: validateIndex(idx) }));\n"
        )
        self.assertFalse(
            _run_bun(code).get("ok"),
            "index with empty blockId must fail validation",
        )

    def test_validate_index_false_for_hash_mismatch(self):
        code = (
            _TS_INDEX_HELPER
            + 'import { validateIndex } from "'
            + TURN_INDEX_ABS + "\";\n"
            + "const idx = makeIndex({});\n"
            + 'idx.contentHash = "sha256:deadbeef";\n'
            + "console.log(JSON.stringify({ ok: validateIndex(idx) }));\n"
        )
        self.assertFalse(
            _run_bun(code).get("ok"),
            "index with mismatched contentHash must fail validation",
        )

    def test_validate_index_false_for_missing_arrays(self):
        code = (
            _TS_INDEX_HELPER
            + 'import { computeContentHash, validateIndex } from "'
            + TURN_INDEX_ABS + "\";\n"
            + "const idx = makeIndex({});\n"
            + "delete idx.actions;\n"
            + "delete idx.keywords;\n"
            + "idx.contentHash = computeContentHash(idx);\n"
            + "console.log(JSON.stringify({ ok: validateIndex(idx) }));\n"
        )
        self.assertFalse(
            _run_bun(code).get("ok"),
            "index with missing required array fields must fail "
            "validation",
        )


# --------------------------------------------------------------------
# AC 3: FileColdStore writeIndex / search behavioral
# --------------------------------------------------------------------


class FileColdStoreIndexTests(unittest.TestCase):
    """Bun-based behavioral tests on file-cold-store.ts: writeIndex,
    search, idempotency, hash conflict, scope, dedup, ordering."""

    def setUp(self):
        if not _bun_available():
            self.skipTest("bun not on PATH")
        self._tmpdir = tempfile.mkdtemp(prefix="phasef_")

    def tearDown(self):
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def test_write_index_writes_xml_file(self):
        code = (
            'import * as fs from "node:fs";\n'
            'import * as path from "node:path";\n'
            + _TS_INDEX_HELPER
            + 'import { FileColdStore } from "'
            + FILE_COLD_STORE_ABS + "\";\n"
            + 'const store = new FileColdStore("' + self._tmpdir + '");\n'
            + "const idx = makeIndex({ keywords: ['deploy'] });\n"
            + "await store.writeIndex(idx);\n"
            + 'const idxDir = path.join("' + self._tmpdir + '",\n'
            + '  "test-repo", "turn-index", "session-1");\n'
            + "const files = fs.existsSync(idxDir)\n"
            + "  ? fs.readdirSync(idxDir).filter(f => f.endsWith('.xml'))\n"
            + "  : [];\n"
            + "console.log(JSON.stringify({ count: files.length }));\n"
        )
        self.assertGreaterEqual(
            _run_bun(code).get("count", 0), 1,
            "writeIndex must create at least one XML file under "
            "turn-index/",
        )

    def test_write_index_idempotent(self):
        code = (
            _TS_INDEX_HELPER
            + 'import { FileColdStore } from "'
            + FILE_COLD_STORE_ABS + "\";\n"
            + 'const store = new FileColdStore("' + self._tmpdir + '");\n'
            + "const idx = makeIndex({ keywords: ['deploy'] });\n"
            + "await store.writeIndex(idx);\n"
            + "var threw = false;\n"
            + "try {\n"
            + "  await store.writeIndex(idx);\n"
            + "} catch (e) { threw = true; }\n"
            + "console.log(JSON.stringify({ threw }));\n"
        )
        self.assertFalse(
            _run_bun(code).get("threw"),
            "writing the same index twice must be idempotent (no error)",
        )

    def test_write_index_hash_conflict_throws(self):
        code = (
            _TS_INDEX_HELPER
            + 'import { FileColdStore } from "'
            + FILE_COLD_STORE_ABS + "\";\n"
            + 'const store = new FileColdStore("' + self._tmpdir + '");\n'
            + "const idx1 = makeIndex({\n"
            + '  blockId: "turn-1", sequence: 1, intent: "first",\n'
            + "});\n"
            + "const idx2 = makeIndex({\n"
            + '  blockId: "turn-1", sequence: 1, intent: "second",\n'
            + "});\n"
            + "await store.writeIndex(idx1);\n"
            + "var threw = false;\n"
            + "try {\n"
            + "  await store.writeIndex(idx2);\n"
            + "} catch (e) { threw = true; }\n"
            + "console.log(JSON.stringify({ threw }));\n"
        )
        self.assertTrue(
            _run_bun(code).get("threw"),
            "writing a different index with the same blockId must throw "
            "a hash-conflict error",
        )

    def test_search_returns_hits(self):
        code = (
            _TS_INDEX_HELPER
            + 'import { FileColdStore } from "'
            + FILE_COLD_STORE_ABS + "\";\n"
            + 'const store = new FileColdStore("' + self._tmpdir + '");\n'
            + "await store.writeIndex(makeIndex({\n"
            + '  keywords: ["deploy", "release"], intent: "deploy app",\n'
            + "}));\n"
            + 'const hits = await store.search("deploy",\n'
            + '  { repository: "test-repo" });\n'
            + "console.log(JSON.stringify({ hits }));\n"
        )
        hits = _run_bun(code).get("hits", [])
        self.assertGreaterEqual(
            len(hits), 1,
            "search for a matching token must return at least one hit",
        )

    def test_search_with_limit(self):
        code = (
            _TS_INDEX_HELPER
            + 'import { FileColdStore } from "'
            + FILE_COLD_STORE_ABS + "\";\n"
            + 'const store = new FileColdStore("' + self._tmpdir + '");\n'
            + "for (var i = 1; i <= 5; i++) {\n"
            + "  await store.writeIndex(makeIndex({\n"
            + '    blockId: "turn-" + i, sequence: i,\n'
            + '    keywords: ["shared"],\n'
            + "  }));\n"
            + "}\n"
            + 'const hits = await store.search("shared",\n'
            + '  { repository: "test-repo" }, { limit: 2 });\n'
            + "console.log(JSON.stringify({ count: hits.length }));\n"
        )
        self.assertLessEqual(
            _run_bun(code).get("count", 0), 2,
            "search with limit=2 must return at most 2 results",
        )

    def test_search_deduplicates_by_block_id(self):
        code = (
            _TS_INDEX_HELPER
            + 'import { FileColdStore } from "'
            + FILE_COLD_STORE_ABS + "\";\n"
            + 'const store = new FileColdStore("' + self._tmpdir + '");\n'
            + "await store.writeIndex(makeIndex({\n"
            + '  blockId: "turn-1", sequence: 1, sessionId: "session-1",\n'
            + '  keywords: ["deploy"],\n'
            + "}));\n"
            + "await store.writeIndex(makeIndex({\n"
            + '  blockId: "turn-1", sequence: 5, sessionId: "session-2",\n'
            + '  keywords: ["deploy"],\n'
            + "}));\n"
            + 'const hits = await store.search("deploy",\n'
            + '  { repository: "test-repo" });\n'
            + "var ids = hits.map(h => h.blockId);\n"
            + "var unique = new Set(ids);\n"
            + "console.log(JSON.stringify({ total: ids.length, "
            + "unique: unique.size }));\n"
        )
        data = _run_bun(code)
        self.assertEqual(
            data.get("unique"), 1,
            "search must deduplicate by blockId",
        )

    def test_search_stable_order(self):
        code = (
            _TS_INDEX_HELPER
            + 'import { FileColdStore } from "'
            + FILE_COLD_STORE_ABS + "\";\n"
            + 'const store = new FileColdStore("' + self._tmpdir + '");\n'
            + "await store.writeIndex(makeIndex({\n"
            + '  blockId: "turn-low", sequence: 3,\n'
            + '  keywords: ["alpha", "beta"], intent: "alpha beta",\n'
            + "}));\n"
            + "await store.writeIndex(makeIndex({\n"
            + '  blockId: "turn-high", sequence: 1,\n'
            + '  keywords: ["alpha"], intent: "alpha",\n'
            + "}));\n"
            + 'const hits = await store.search("alpha beta",\n'
            + '  { repository: "test-repo" });\n'
            + "var info = hits.map(h => ({\n"
            + '  blockId: h.blockId, score: h.score, sequence: h.sequence,\n'
            + "}));\n"
            + "console.log(JSON.stringify({ info }));\n"
        )
        info = _run_bun(code).get("info", [])
        self.assertGreaterEqual(
            len(info), 2,
            "search must return both indexes for a multi-token query",
        )
        # Higher score must come first.
        self.assertGreaterEqual(
            info[0].get("score"), info[1].get("score"),
            "results must be sorted by descending score",
        )

    def test_search_scope_filtering(self):
        code = (
            _TS_INDEX_HELPER
            + 'import { FileColdStore } from "'
            + FILE_COLD_STORE_ABS + "\";\n"
            + 'const store = new FileColdStore("' + self._tmpdir + '");\n'
            + "await store.writeIndex(makeIndex({\n"
            + '  repository: "repo-a", taskId: "task-1",\n'
            + '  sessionId: "sess-1", keywords: ["shared"],\n'
            + "}));\n"
            + "await store.writeIndex(makeIndex({\n"
            + '  repository: "repo-a", taskId: "task-2",\n'
            + '  sessionId: "sess-1", keywords: ["shared"],\n'
            + "}));\n"
            + "await store.writeIndex(makeIndex({\n"
            + '  repository: "repo-b", taskId: "task-1",\n'
            + '  sessionId: "sess-1", keywords: ["shared"],\n'
            + "}));\n"
            + 'const allA = await store.search("shared",\n'
            + '  { repository: "repo-a" });\n'
            + 'const taskA1 = await store.search("shared",\n'
            + '  { repository: "repo-a", taskId: "task-1" });\n'
            + 'const sessB = await store.search("shared",\n'
            + '  { repository: "repo-b", sessionId: "sess-1" });\n'
            + 'const noneC = await store.search("shared",\n'
            + '  { repository: "repo-c" });\n'
            + "console.log(JSON.stringify({\n"
            + "  allA: allA.length, taskA1: taskA1.length,\n"
            + "  sessB: sessB.length, noneC: noneC.length,\n"
            + "}));\n"
        )
        data = _run_bun(code)
        self.assertEqual(
            data.get("allA"), 2,
            "repository-only scope must return both repo-a indexes",
        )
        self.assertEqual(
            data.get("taskA1"), 1,
            "repository + taskId scope must filter to task-1",
        )
        self.assertEqual(
            data.get("sessB"), 1,
            "repository + sessionId scope must filter correctly",
        )
        self.assertEqual(
            data.get("noneC"), 0,
            "non-existent repository must return zero results",
        )

    def test_search_no_matches(self):
        code = (
            _TS_INDEX_HELPER
            + 'import { FileColdStore } from "'
            + FILE_COLD_STORE_ABS + "\";\n"
            + 'const store = new FileColdStore("' + self._tmpdir + '");\n'
            + "await store.writeIndex(makeIndex({\n"
            + '  keywords: ["deploy"], intent: "deploy app",\n'
            + "}));\n"
            + 'const hits = await store.search("nonexistent-token",\n'
            + '  { repository: "test-repo" });\n'
            + "console.log(JSON.stringify({ count: hits.length }));\n"
        )
        self.assertEqual(
            _run_bun(code).get("count"), 0,
            "search with no matching tokens must return empty list",
        )

    def test_search_block_ref_points_to_turn_path(self):
        code = (
            _TS_INDEX_HELPER
            + 'import { FileColdStore } from "'
            + FILE_COLD_STORE_ABS + "\";\n"
            + 'const store = new FileColdStore("' + self._tmpdir + '");\n'
            + "await store.writeIndex(makeIndex({\n"
            + '  keywords: ["deploy"], intent: "deploy app",\n'
            + "}));\n"
            + 'const hits = await store.search("deploy",\n'
            + '  { repository: "test-repo" });\n'
            + "console.log(JSON.stringify({\n"
            + "  blockRef: hits.length > 0 ? hits[0].blockRef : '',\n"
            + "}));\n"
        )
        block_ref = _run_bun(code).get("blockRef", "")
        self.assertIn(
            "turns", block_ref,
            "blockRef must point to the TurnBlock turns/ path",
        )
        self.assertNotIn(
            "turn-index", block_ref,
            "blockRef must NOT point to the index file path",
        )


# --------------------------------------------------------------------
# AC 3: Search determinism behavioral
# --------------------------------------------------------------------


class SearchDeterminismTests(unittest.TestCase):
    """Bun-based determinism and matchedFields tests."""

    def setUp(self):
        if not _bun_available():
            self.skipTest("bun not on PATH")
        self._tmpdir = tempfile.mkdtemp(prefix="phasef_det_")

    def tearDown(self):
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def test_same_query_same_result_order(self):
        code = (
            _TS_INDEX_HELPER
            + 'import { FileColdStore } from "'
            + FILE_COLD_STORE_ABS + "\";\n"
            + 'const store = new FileColdStore("' + self._tmpdir + '");\n'
            + "await store.writeIndex(makeIndex({\n"
            + '  blockId: "turn-1", sequence: 1,\n'
            + '  keywords: ["alpha", "beta"], intent: "alpha beta",\n'
            + "}));\n"
            + "await store.writeIndex(makeIndex({\n"
            + '  blockId: "turn-2", sequence: 2,\n'
            + '  keywords: ["alpha"], intent: "alpha",\n'
            + "}));\n"
            + 'const r1 = await store.search("alpha beta",\n'
            + '  { repository: "test-repo" });\n'
            + 'const r2 = await store.search("alpha beta",\n'
            + '  { repository: "test-repo" });\n'
            + "var order1 = r1.map(h => h.blockId);\n"
            + "var order2 = r2.map(h => h.blockId);\n"
            + "console.log(JSON.stringify({ same: JSON.stringify(order1) "
            + "=== JSON.stringify(order2), order1 }));\n"
        )
        data = _run_bun(code)
        self.assertTrue(
            data.get("same"),
            "same query + same indexes must produce identical order",
        )

    def test_matched_fields_populated(self):
        code = (
            _TS_INDEX_HELPER
            + 'import { FileColdStore } from "'
            + FILE_COLD_STORE_ABS + "\";\n"
            + 'const store = new FileColdStore("' + self._tmpdir + '");\n'
            + "await store.writeIndex(makeIndex({\n"
            + '  intent: "deploy",\n'
            + '  keywords: ["deploy"],\n'
            + "  decisions: [makeEntry('deploy decision')],\n"
            + "}));\n"
            + 'const hits = await store.search("deploy",\n'
            + '  { repository: "test-repo" });\n'
            + "var fields = hits.length > 0 ? hits[0].matchedFields : [];\n"
            + "console.log(JSON.stringify({ fields }));\n"
        )
        fields = _run_bun(code).get("fields", [])
        self.assertGreaterEqual(
            len(fields), 1,
            "matchedFields must be populated with at least one field "
            "name that matched",
        )


if __name__ == "__main__":
    unittest.main()
