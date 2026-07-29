"""Requirement tests for Phase E: rule cache (准则 cache).

Phase E (docs/teamflow-memory-context-design.md §9) implements the
protected rule cache — a stable, auditable XML schema of rules with
authority levels, a ``memory_delta`` incremental format (assert /
supersede / retire), an authority-aware reducer with incremental
semantics, and integration into the existing extension hooks.

The rule cache is the long-lived constraint layer: project/role
boundaries, user constraints, acceptance criteria, accepted
decisions, explicit non-goals, and safety requirements.  Rules are
never evicted from the visible context; low-authority sources cannot
override high-authority rules; user rules are only superseded by
user-or-higher authority; inferred content enters only as
``candidate``; tool evidence must reference the original event.

Test approaches (mirrors existing Phase B / Phase D conventions):

1. Source-text assertions — read the TypeScript source and assert
   required code patterns exist via ``re.compile`` / ``in`` checks.
2. Bun-based behavioral tests — write inline TypeScript to a temp
   file, import the actual module, run with ``bun run``, parse JSON
   output.  Skipped gracefully if ``bun`` is not on PATH.

Contracts defined by these tests (the implementer MUST export):

rule-cache.ts
  - interface Rule  — fields: id, key, kind, authority, status,
    scope, source, content, contentHash
  - interface RuleCache — fields: version, taskId, rules: Rule[],
    contentHash
  - MemoryDelta operations: assert / supersede / retire
  - Authority levels: repository, system_policy, user, planner,
    tool_evidence, candidate
  - function serialize(cache: RuleCache): string
  - function deserialize(xml: string): RuleCache
  - function computeContentHash(cache: RuleCache): string
  - function serializeRuleDelta(delta: MemoryDelta): string

rule-cache-reducer.ts
  - function applyDelta(cache: RuleCache, delta: MemoryDelta):
    RuleCache — rejected operations (authority too low, invalid
    source, structural failure) are silently skipped.  Pure and
    total.

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

RULE_CACHE_FILE = (
    ROOT / ".teamflow" / "extensions" / "memory-context" / "rule-cache.ts"
)
RULE_CACHE_REDUCER_FILE = (
    ROOT / ".teamflow" / "extensions" / "memory-context"
    / "rule-cache-reducer.ts"
)
EXTENSION_FILE = (
    ROOT / ".teamflow" / "extensions" / "memory-context" / "index.ts"
)
README_FILE = ROOT / "README.md"
DESIGN_DOC = ROOT / "docs" / "teamflow-memory-context-design.md"
DOCTOR_FILE = ROOT / "scripts" / "doctor.sh"
INIT_FILE = ROOT / "scripts" / "install"

RULE_CACHE_ABS = str(RULE_CACHE_FILE)
RULE_CACHE_REDUCER_ABS = str(RULE_CACHE_REDUCER_FILE)


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


# Inline TypeScript helpers shared by bun tests ----------------------

_TS_RULE_HELPER = """\
function makeRule(o) {
  return Object.assign({
    id: "rule-1", key: "k", kind: "constraint", authority: "user",
    status: "active", scope: "task", source: "memory://turn-1#u1",
    content: "default", contentHash: "",
  }, o || {});
}
function makeCache(rules) {
  return { version: 1, taskId: "task-1", rules: rules || [], contentHash: "" };
}
function makeDelta(operations) {
  return { operations: operations || [] };
}
function normalize(c) {
  return JSON.stringify((c.rules || []).map(function (r) {
    return { key: r.key, authority: r.authority, status: r.status,
             content: r.content, source: r.source, kind: r.kind };
  }));
}
"""


# --------------------------------------------------------------------
# AC 1: Rule / RuleCache / MemoryDelta schema (rule-cache.ts)
# --------------------------------------------------------------------


class RuleCacheSchemaTests(unittest.TestCase):
    """Source-text assertions on rule-cache.ts: interfaces, authority
    levels, serialize/deserialize/hash functions."""

    def setUp(self):
        self.text = (
            RULE_CACHE_FILE.read_text(encoding="utf-8")
            if RULE_CACHE_FILE.is_file()
            else ""
        )

    def test_rule_cache_file_exists(self):
        self.assertTrue(
            RULE_CACHE_FILE.is_file(),
            "rule-cache.ts must exist under memory-context/",
        )

    def test_defines_rule_interface(self):
        self.assertTrue(
            "interface Rule" in self.text or "type Rule" in self.text,
            "source must define 'interface Rule' or 'type Rule'",
        )

    def test_rule_interface_has_required_fields(self):
        for field in ("key", "kind", "authority", "status", "scope",
                       "source", "content"):
            with self.subTest(field=field):
                self.assertIn(
                    field,
                    self.text,
                    f"Rule interface must define field '{field}'",
                )
        self.assertTrue(
            "contentHash" in self.text or "content_hash" in self.text,
            "Rule must have a contentHash / content_hash field",
        )

    def test_defines_rule_cache_interface(self):
        self.assertTrue(
            "interface RuleCache" in self.text
            or "type RuleCache" in self.text,
            "source must define 'interface RuleCache' or 'type RuleCache'",
        )

    def test_rule_cache_has_required_fields(self):
        for token in ("version", "rules"):
            with self.subTest(token=token):
                self.assertIn(token, self.text)
        self.assertTrue(
            "taskId" in self.text or "task_id" in self.text,
            "RuleCache must reference taskId / task_id",
        )

    def test_defines_memory_delta(self):
        self.assertTrue(
            "MemoryDelta" in self.text or "memory_delta" in self.text,
            "source must define MemoryDelta type or memory_delta",
        )

    def test_memory_delta_has_operations(self):
        for op in ("assert", "supersede", "retire"):
            with self.subTest(op=op):
                self.assertIn(
                    op,
                    self.text,
                    f"memory_delta must define '{op}' operation",
                )

    def test_authority_levels_defined(self):
        for level in (
            "repository",
            "system_policy",
            "user",
            "planner",
            "tool_evidence",
            "candidate",
        ):
            with self.subTest(level=level):
                self.assertIn(
                    level,
                    self.text,
                    f"authority level '{level}' must be defined",
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
        self.assertIn(
            "sha256",
            self.text.lower(),
            "hash must use SHA-256",
        )

    def test_has_rule_delta_serializer(self):
        self.assertTrue(
            "serializeRuleDelta" in self.text
            or ("serialize" in self.text and "delta" in self.text.lower()),
            "source must define a memory_delta serialize function "
            "(serializeRuleDelta or equivalent)",
        )

    def test_uses_node_crypto(self):
        self.assertIn(
            "node:crypto",
            self.text,
            "source must import from 'node:crypto'",
        )

    def test_has_xml_escaping(self):
        self.assertTrue(
            "&amp;" in self.text
            or "escapeXml" in self.text
            or "escape" in self.text.lower(),
            "source must handle XML entity escaping",
        )


# --------------------------------------------------------------------
# AC 1: serialize / deserialize / hash behavioral (rule-cache.ts)
# --------------------------------------------------------------------


class RuleCacheSerializeTests(unittest.TestCase):
    """Bun-based behavioral tests on rule-cache.ts serialize,
    deserialize, and computeContentHash."""

    def setUp(self):
        if not _bun_available():
            self.skipTest("bun not on PATH")

    def test_serialize_produces_canonical_xml(self):
        code = (
            _TS_RULE_HELPER
            + 'import { serialize } from "'
            + RULE_CACHE_ABS + "\";\n"
            + "const cache = makeCache([makeRule({"
            + ' key: "context.test", content: "No compaction." })]);\n'
            + 'console.log(JSON.stringify({ xml: serialize(cache) }));\n'
        )
        xml = _run_bun(code).get("xml", "")
        self.assertTrue(
            xml.startswith("<rule_cache"),
            f"serialized XML must start with <rule_cache: {xml!r}",
        )
        self.assertIn(
            "content_hash",
            xml,
            "serialized XML must include content_hash attribute",
        )

    def test_deserialize_roundtrip(self):
        code = (
            _TS_RULE_HELPER
            + 'import { serialize, deserialize } from "'
            + RULE_CACHE_ABS + "\";\n"
            + "const cache = makeCache([makeRule({\n"
            + '  id: "rule-7", key: "context.no_compact",\n'
            + '  kind: "constraint", authority: "user",\n'
            + '  status: "active", scope: "task",\n'
            + '  source: "memory://turn-18#u1",\n'
            + '  content: "Do not compact.",\n'
            + "})]);\n"
            + "const xml = serialize(cache);\n"
            + "const restored = deserialize(xml);\n"
            + "console.log(JSON.stringify({ restored }));\n"
        )
        restored = _run_bun(code).get("restored", {})
        self.assertEqual(restored.get("version"), 1)
        self.assertEqual(restored.get("taskId"), "task-1")
        rules = restored.get("rules", [])
        self.assertEqual(len(rules), 1)
        r = rules[0]
        self.assertEqual(r.get("key"), "context.no_compact")
        self.assertEqual(r.get("kind"), "constraint")
        self.assertEqual(r.get("authority"), "user")
        self.assertEqual(r.get("status"), "active")
        self.assertEqual(r.get("scope"), "task")
        self.assertEqual(r.get("source"), "memory://turn-18#u1")
        self.assertEqual(r.get("content"), "Do not compact.")

    def test_content_hash_deterministic(self):
        code = (
            _TS_RULE_HELPER
            + 'import { computeContentHash } from "'
            + RULE_CACHE_ABS + "\";\n"
            + "const cache = makeCache([makeRule({\n"
            + '  key: "k1", content: "stable",\n'
            + "})]);\n"
            + "const h1 = computeContentHash(cache);\n"
            + "const h2 = computeContentHash(cache);\n"
            + "console.log(JSON.stringify({ h1, h2 }));\n"
        )
        data = _run_bun(code)
        self.assertEqual(
            data.get("h1"), data.get("h2"),
            "same cache must produce identical hash",
        )

    def test_content_hash_excludes_hash_field(self):
        code = (
            _TS_RULE_HELPER
            + 'import { computeContentHash } from "'
            + RULE_CACHE_ABS + "\";\n"
            + "const base = makeCache([makeRule({\n"
            + '  key: "k1", content: "content",\n'
            + "})]);\n"
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

    def test_content_hash_is_sha256_format(self):
        code = (
            _TS_RULE_HELPER
            + 'import { computeContentHash } from "'
            + RULE_CACHE_ABS + "\";\n"
            + "const cache = makeCache([]);\n"
            + "console.log(JSON.stringify({ hash: "
            + "computeContentHash(cache) }));\n"
        )
        h = _run_bun(code).get("hash", "")
        self.assertTrue(
            h.startswith("sha256:"),
            f"hash must start with 'sha256:': {h!r}",
        )
        hex_part = h[len("sha256:"):]
        self.assertEqual(len(hex_part), 64)
        self.assertTrue(
            all(c in "0123456789abcdef" for c in hex_part),
            f"hex digest must be lowercase hex: {hex_part!r}",
        )

    def test_xml_escaping_in_attributes_and_content(self):
        code = (
            _TS_RULE_HELPER
            + 'import { serialize } from "'
            + RULE_CACHE_ABS + "\";\n"
            + "const cache = makeCache([makeRule({\n"
            + '  key: "a&b<c>", content: "x&y<z>w",\n'
            + "})]);\n"
            + 'console.log(JSON.stringify({ xml: serialize(cache) }));\n'
        )
        xml = _run_bun(code).get("xml", "")
        self.assertIn("&amp;", xml)
        self.assertIn("&lt;", xml)
        self.assertNotIn("a&b", xml)

    def test_serialize_rule_delta_produces_memory_delta_xml(self):
        code = (
            _TS_RULE_HELPER
            + 'import { serializeRuleDelta } from "'
            + RULE_CACHE_ABS + "\";\n"
            + "const delta = makeDelta([\n"
            + '  { op: "assert", key: "k1", kind: "constraint",\n'
            + '    authority: "user", scope: "task",\n'
            + '    source: "turn-1:u1", content: "rule text" },\n'
            + "]);\n"
            + 'console.log(JSON.stringify({ xml: '
            + "serializeRuleDelta(delta) }));\n"
        )
        xml = _run_bun(code).get("xml", "")
        self.assertTrue(
            xml.strip().startswith("<memory_delta"),
            f"delta XML must start with <memory_delta: {xml!r}",
        )
        self.assertIn("<assert", xml)


# --------------------------------------------------------------------
# AC 1: MemoryDelta schema source-text (rule-cache.ts)
# --------------------------------------------------------------------


class MemoryDeltaSchemaTests(unittest.TestCase):
    """Source-text assertions on memory_delta serialization."""

    def setUp(self):
        self.text = (
            RULE_CACHE_FILE.read_text(encoding="utf-8")
            if RULE_CACHE_FILE.is_file()
            else ""
        )

    def test_memory_delta_xml_tag_present(self):
        self.assertIn(
            "memory_delta",
            self.text,
            "source must produce <memory_delta> XML",
        )

    def test_assert_operation_serialized(self):
        self.assertIn(
            "assert",
            self.text,
            "memory_delta must serialize assert operations",
        )

    def test_supersede_operation_serialized(self):
        self.assertIn(
            "supersede",
            self.text,
            "memory_delta must serialize supersede operations",
        )

    def test_retire_operation_serialized(self):
        self.assertIn(
            "retire",
            self.text,
            "memory_delta must serialize retire operations",
        )

    def test_rule_element_in_delta(self):
        self.assertIn(
            "rule",
            self.text,
            "memory_delta assert must embed a <rule> element",
        )


# --------------------------------------------------------------------
# AC 2: Incremental semantics (rule-cache-reducer.ts)
# --------------------------------------------------------------------


class RuleCacheReducerTests(unittest.TestCase):
    """Bun-based behavioral tests on rule-cache-reducer.ts: incremental
    preservation, assert, supersede, retire, idempotency, replayability."""

    def setUp(self):
        if not _bun_available():
            self.skipTest("bun not on PATH")

    def test_not_mentioning_rule_preserves_it(self):
        code = (
            _TS_RULE_HELPER
            + 'import { applyDelta } from "'
            + RULE_CACHE_REDUCER_ABS + "\";\n"
            + "const cache = makeCache([\n"
            + '  makeRule({ id: "ra", key: "key-a", content: "rule A",'
            + ' authority: "user", status: "active" }),\n'
            + '  makeRule({ id: "rb", key: "key-b", content: "rule B",'
            + ' authority: "planner", status: "active" }),\n'
            + "]);\n"
            + "const delta = makeDelta([\n"
            + '  { op: "assert", key: "key-c", kind: "constraint",\n'
            + '    authority: "user", scope: "task",\n'
            + '    source: "u2", content: "rule C" },\n'
            + "]);\n"
            + "const result = applyDelta(cache, delta);\n"
            + "console.log(JSON.stringify(result));\n"
        )
        result = _run_bun(code)
        rules = {r.get("key"): r for r in result.get("rules", [])}
        self.assertIn("key-a", rules, "rule A must be preserved")
        self.assertIn("key-b", rules, "rule B must be preserved")
        self.assertEqual(rules["key-a"].get("content"), "rule A")
        self.assertEqual(rules["key-b"].get("content"), "rule B")
        self.assertIn("key-c", rules, "rule C must be added")

    def test_assert_adds_new_rule(self):
        code = (
            _TS_RULE_HELPER
            + 'import { applyDelta } from "'
            + RULE_CACHE_REDUCER_ABS + "\";\n"
            + "const cache = makeCache([]);\n"
            + "const delta = makeDelta([\n"
            + '  { op: "assert", key: "new-key", kind: "decision",\n'
            + '    authority: "user", scope: "task",\n'
            + '    source: "u1", content: "new rule body" },\n'
            + "]);\n"
            + "const result = applyDelta(cache, delta);\n"
            + "console.log(JSON.stringify(result));\n"
        )
        rules = _run_bun(code).get("rules", [])
        new_rules = [r for r in rules if r.get("key") == "new-key"]
        self.assertTrue(
            len(new_rules) >= 1,
            "assert must add the new rule to the cache",
        )
        self.assertEqual(new_rules[0].get("content"), "new rule body")

    def test_assert_idempotent(self):
        code = (
            _TS_RULE_HELPER
            + 'import { applyDelta } from "'
            + RULE_CACHE_REDUCER_ABS + "\";\n"
            + "const delta = makeDelta([\n"
            + '  { op: "assert", key: "k1", kind: "constraint",\n'
            + '    authority: "user", scope: "task",\n'
            + '    source: "u1", content: "same" },\n'
            + "]);\n"
            + "const cache0 = makeCache([]);\n"
            + "const r1 = applyDelta(cache0, delta);\n"
            + "const r2 = applyDelta(r1, delta);\n"
            + "console.log(JSON.stringify({\n"
            + "  s1: normalize(r1), s2: normalize(r2),\n"
            + "}));\n"
        )
        data = _run_bun(code)
        self.assertEqual(
            data.get("s1"), data.get("s2"),
            "applying the same assert twice must be idempotent",
        )

    def test_supersede_marks_old_and_adds_new(self):
        code = (
            _TS_RULE_HELPER
            + 'import { applyDelta } from "'
            + RULE_CACHE_REDUCER_ABS + "\";\n"
            + "const cache = makeCache([\n"
            + '  makeRule({ id: "r1", key: "k1", content: "old",\n'
            + '    authority: "user", status: "active" }),\n'
            + "]);\n"
            + "const delta = makeDelta([\n"
            + '  { op: "supersede", key: "k1", authority: "user",\n'
            + '    source: "u2", content: "new content" },\n'
            + "]);\n"
            + "const result = applyDelta(cache, delta);\n"
            + "console.log(JSON.stringify(result.rules));\n"
        )
        rules = _run_bun(code)
        k1_rules = [r for r in rules if r.get("key") == "k1"]
        superseded = [
            r for r in k1_rules if r.get("status") == "superseded"
        ]
        self.assertTrue(
            len(superseded) >= 1,
            "old rule must be marked superseded",
        )
        active_new = [
            r for r in k1_rules
            if r.get("status") == "active"
            and r.get("content") == "new content"
        ]
        self.assertTrue(
            len(active_new) >= 1,
            "new rule with new content must be active",
        )

    def test_retire_marks_retired_not_deleted(self):
        code = (
            _TS_RULE_HELPER
            + 'import { applyDelta } from "'
            + RULE_CACHE_REDUCER_ABS + "\";\n"
            + "const cache = makeCache([\n"
            + '  makeRule({ id: "r1", key: "k1", content: "rule",\n'
            + '    authority: "candidate", status: "candidate" }),\n'
            + "]);\n"
            + "const delta = makeDelta([\n"
            + '  { op: "retire", key: "k1", authority: "user",\n'
            + '    source: "u1" },\n'
            + "]);\n"
            + "const result = applyDelta(cache, delta);\n"
            + "console.log(JSON.stringify(result.rules));\n"
        )
        rules = _run_bun(code)
        k1_rules = [r for r in rules if r.get("key") == "k1"]
        self.assertTrue(
            len(k1_rules) >= 1,
            "retired rule must remain in the cache for audit",
        )
        retired = [r for r in k1_rules if r.get("status") == "retired"]
        self.assertTrue(
            len(retired) >= 1,
            "rule must be marked retired",
        )

    def test_deterministic_replay(self):
        code = (
            _TS_RULE_HELPER
            + 'import { applyDelta } from "'
            + RULE_CACHE_REDUCER_ABS + "\";\n"
            + "const d1 = makeDelta([\n"
            + '  { op: "assert", key: "k1", kind: "constraint",\n'
            + '    authority: "user", scope: "task",\n'
            + '    source: "u1", content: "c1" },\n'
            + "]);\n"
            + "const d2 = makeDelta([\n"
            + '  { op: "assert", key: "k2", kind: "decision",\n'
            + '    authority: "planner", scope: "task",\n'
            + '    source: "p1", content: "c2" },\n'
            + "]);\n"
            + "const d3 = makeDelta([\n"
            + '  { op: "retire", key: "k2", authority: "planner",\n'
            + '    source: "p2" },\n'
            + "]);\n"
            + "function run(c) {\n"
            + "  return applyDelta(applyDelta(applyDelta(c, d1), d2), d3);\n"
            + "}\n"
            + "const cache0 = makeCache([]);\n"
            + "const s1 = normalize(run(cache0));\n"
            + "const s2 = normalize(run(cache0));\n"
            + "console.log(JSON.stringify({ s1, s2 }));\n"
        )
        data = _run_bun(code)
        self.assertEqual(
            data.get("s1"), data.get("s2"),
            "same delta sequence must produce identical cache state",
        )


# --------------------------------------------------------------------
# AC 3: Authority ordering (rule-cache-reducer.ts)
# --------------------------------------------------------------------


class ReducerAuthorityTests(unittest.TestCase):
    """Bun-based authority ordering: low authority cannot override or
    retire high authority."""

    def setUp(self):
        if not _bun_available():
            self.skipTest("bun not on PATH")

    def test_candidate_cannot_update_user_rule(self):
        code = (
            _TS_RULE_HELPER
            + 'import { applyDelta } from "'
            + RULE_CACHE_REDUCER_ABS + "\";\n"
            + "const cache = makeCache([\n"
            + '  makeRule({ id: "r1", key: "k1", authority: "user",\n'
            + '    status: "active", content: "original" }),\n'
            + "]);\n"
            + "const delta = makeDelta([\n"
            + '  { op: "assert", key: "k1", kind: "constraint",\n'
            + '    authority: "candidate", scope: "task",\n'
            + '    source: "inf1", content: "override" },\n'
            + "]);\n"
            + "const result = applyDelta(cache, delta);\n"
            + "const k1 = result.rules.filter(r => r.key === \"k1\");\n"
            + "console.log(JSON.stringify(k1));\n"
        )
        rules = _run_bun(code)
        self.assertTrue(
            any(r.get("authority") == "user"
                and r.get("content") == "original"
                and r.get("status") == "active"
                for r in rules),
            "original user rule must be unchanged",
        )
        self.assertFalse(
            any(r.get("content") == "override" for r in rules),
            "candidate assert must be rejected entirely",
        )

    def test_user_can_update_candidate_rule(self):
        code = (
            _TS_RULE_HELPER
            + 'import { applyDelta } from "'
            + RULE_CACHE_REDUCER_ABS + "\";\n"
            + "const cache = makeCache([\n"
            + '  makeRule({ id: "r1", key: "k1", authority: "candidate",\n'
            + '    status: "candidate", content: "original" }),\n'
            + "]);\n"
            + "const delta = makeDelta([\n"
            + '  { op: "assert", key: "k1", kind: "constraint",\n'
            + '    authority: "user", scope: "task",\n'
            + '    source: "u1", content: "updated" },\n'
            + "]);\n"
            + "const result = applyDelta(cache, delta);\n"
            + "const k1 = result.rules.filter(r => r.key === \"k1\");\n"
            + "console.log(JSON.stringify(k1));\n"
        )
        rules = _run_bun(code)
        updated = [
            r for r in rules
            if r.get("authority") == "user"
            and r.get("content") == "updated"
        ]
        self.assertTrue(
            len(updated) >= 1,
            "user assert must update the candidate rule",
        )

    def test_equal_authority_can_update_content(self):
        code = (
            _TS_RULE_HELPER
            + 'import { applyDelta } from "'
            + RULE_CACHE_REDUCER_ABS + "\";\n"
            + "const cache = makeCache([\n"
            + '  makeRule({ id: "r1", key: "k1", authority: "user",\n'
            + '    status: "active", content: "original" }),\n'
            + "]);\n"
            + "const delta = makeDelta([\n"
            + '  { op: "assert", key: "k1", kind: "constraint",\n'
            + '    authority: "user", scope: "task",\n'
            + '    source: "u2", content: "updated" },\n'
            + "]);\n"
            + "const result = applyDelta(cache, delta);\n"
            + "const k1 = result.rules.filter(r => r.key === \"k1\");\n"
            + "console.log(JSON.stringify(k1));\n"
        )
        rules = _run_bun(code)
        updated = [
            r for r in rules
            if r.get("content") == "updated"
            and r.get("authority") == "user"
        ]
        self.assertTrue(
            len(updated) >= 1,
            "equal authority must be able to update content",
        )

    def test_candidate_cannot_retire_user_rule(self):
        code = (
            _TS_RULE_HELPER
            + 'import { applyDelta } from "'
            + RULE_CACHE_REDUCER_ABS + "\";\n"
            + "const cache = makeCache([\n"
            + '  makeRule({ id: "r1", key: "k1", authority: "user",\n'
            + '    status: "active", content: "user rule" }),\n'
            + "]);\n"
            + "const delta = makeDelta([\n"
            + '  { op: "retire", key: "k1", authority: "candidate",\n'
            + '    source: "inf1" },\n'
            + "]);\n"
            + "const result = applyDelta(cache, delta);\n"
            + "const k1 = result.rules.filter(r => r.key === \"k1\");\n"
            + "console.log(JSON.stringify(k1));\n"
        )
        rules = _run_bun(code)
        active = [
            r for r in rules
            if r.get("status") == "active"
            and r.get("authority") == "user"
        ]
        self.assertTrue(
            len(active) >= 1,
            "user rule must remain active — candidate retire rejected",
        )

    def test_user_can_retire_candidate_rule(self):
        code = (
            _TS_RULE_HELPER
            + 'import { applyDelta } from "'
            + RULE_CACHE_REDUCER_ABS + "\";\n"
            + "const cache = makeCache([\n"
            + '  makeRule({ id: "r1", key: "k1", authority: "candidate",\n'
            + '    status: "candidate", content: "cand rule" }),\n'
            + "]);\n"
            + "const delta = makeDelta([\n"
            + '  { op: "retire", key: "k1", authority: "user",\n'
            + '    source: "u1" },\n'
            + "]);\n"
            + "const result = applyDelta(cache, delta);\n"
            + "const k1 = result.rules.filter(r => r.key === \"k1\");\n"
            + "console.log(JSON.stringify(k1));\n"
        )
        rules = _run_bun(code)
        retired = [r for r in rules if r.get("status") == "retired"]
        self.assertTrue(
            len(retired) >= 1,
            "user retire must succeed on a candidate rule",
        )

    def test_candidate_cannot_supersede_user_rule(self):
        code = (
            _TS_RULE_HELPER
            + 'import { applyDelta } from "'
            + RULE_CACHE_REDUCER_ABS + "\";\n"
            + "const cache = makeCache([\n"
            + '  makeRule({ id: "r1", key: "k1", authority: "user",\n'
            + '    status: "active", content: "user rule" }),\n'
            + "]);\n"
            + "const delta = makeDelta([\n"
            + '  { op: "supersede", key: "k1", authority: "candidate",\n'
            + '    source: "inf1", content: "cand override" },\n'
            + "]);\n"
            + "const result = applyDelta(cache, delta);\n"
            + "const k1 = result.rules.filter(r => r.key === \"k1\");\n"
            + "console.log(JSON.stringify(k1));\n"
        )
        rules = _run_bun(code)
        self.assertTrue(
            any(r.get("authority") == "user"
                and r.get("content") == "user rule"
                and r.get("status") == "active"
                for r in rules),
            "user rule must be unchanged — candidate supersede rejected",
        )
        self.assertFalse(
            any(r.get("content") == "cand override" for r in rules),
            "candidate supersede content must not appear",
        )


# --------------------------------------------------------------------
# AC 4: User-rule protection, candidate status, tool evidence source
# --------------------------------------------------------------------


class ReducerValidationTests(unittest.TestCase):
    """Bun-based validation: user-rule protection, candidate-only
    inferences, tool_evidence source requirement."""

    def setUp(self):
        if not _bun_available():
            self.skipTest("bun not on PATH")

    def test_planner_cannot_supersede_user_rule(self):
        code = (
            _TS_RULE_HELPER
            + 'import { applyDelta } from "'
            + RULE_CACHE_REDUCER_ABS + "\";\n"
            + "const cache = makeCache([\n"
            + '  makeRule({ id: "r1", key: "k1", authority: "user",\n'
            + '    status: "active", content: "user rule" }),\n'
            + "]);\n"
            + "const delta = makeDelta([\n"
            + '  { op: "supersede", key: "k1", authority: "planner",\n'
            + '    source: "p1", content: "planner override" },\n'
            + "]);\n"
            + "const result = applyDelta(cache, delta);\n"
            + "const k1 = result.rules.filter(r => r.key === \"k1\");\n"
            + "console.log(JSON.stringify(k1));\n"
        )
        rules = _run_bun(code)
        self.assertTrue(
            any(r.get("authority") == "user"
                and r.get("content") == "user rule"
                and r.get("status") == "active"
                for r in rules),
            "planner must not supersede a user rule",
        )
        self.assertFalse(
            any(r.get("content") == "planner override" for r in rules),
            "planner supersede content must not appear",
        )

    def test_candidate_assert_gets_candidate_status(self):
        code = (
            _TS_RULE_HELPER
            + 'import { applyDelta } from "'
            + RULE_CACHE_REDUCER_ABS + "\";\n"
            + "const cache = makeCache([]);\n"
            + "const delta = makeDelta([\n"
            + '  { op: "assert", key: "k1", kind: "inference",\n'
            + '    authority: "candidate", scope: "task",\n'
            + '    source: "inf1", content: "inferred" },\n'
            + "]);\n"
            + "const result = applyDelta(cache, delta);\n"
            + "const k1 = result.rules.filter(r => r.key === \"k1\");\n"
            + "console.log(JSON.stringify(k1));\n"
        )
        rules = _run_bun(code)
        self.assertTrue(len(rules) >= 1, "candidate rule must be added")
        for r in rules:
            self.assertEqual(
                r.get("status"), "candidate",
                "candidate authority rule must have status 'candidate'",
            )

    def test_tool_evidence_empty_source_rejected(self):
        code = (
            _TS_RULE_HELPER
            + 'import { applyDelta } from "'
            + RULE_CACHE_REDUCER_ABS + "\";\n"
            + "const cache = makeCache([]);\n"
            + "const delta = makeDelta([\n"
            + '  { op: "assert", key: "k1", kind: "evidence",\n'
            + '    authority: "tool_evidence", scope: "task",\n'
            + '    source: "", content: "no source" },\n'
            + "]);\n"
            + "const result = applyDelta(cache, delta);\n"
            + "console.log(JSON.stringify(result.rules));\n"
        )
        rules = _run_bun(code)
        self.assertEqual(
            len(rules), 0,
            "tool_evidence with empty source must be rejected",
        )

    def test_tool_evidence_with_source_accepted(self):
        code = (
            _TS_RULE_HELPER
            + 'import { applyDelta } from "'
            + RULE_CACHE_REDUCER_ABS + "\";\n"
            + "const cache = makeCache([]);\n"
            + "const delta = makeDelta([\n"
            + '  { op: "assert", key: "k1", kind: "evidence",\n'
            + '    authority: "tool_evidence", scope: "task",\n'
            + '    source: "turn-18:tool-result-3",'
            + ' content: "verified" },\n'
            + "]);\n"
            + "const result = applyDelta(cache, delta);\n"
            + "console.log(JSON.stringify(result.rules));\n"
        )
        rules = _run_bun(code)
        k1 = [r for r in rules if r.get("key") == "k1"]
        self.assertTrue(
            len(k1) >= 1,
            "tool_evidence with non-empty source must be accepted",
        )

    def test_inferred_rule_never_active(self):
        code = (
            _TS_RULE_HELPER
            + 'import { applyDelta } from "'
            + RULE_CACHE_REDUCER_ABS + "\";\n"
            + "const cache = makeCache([]);\n"
            + "const delta = makeDelta([\n"
            + '  { op: "assert", key: "k1", kind: "inference",\n'
            + '    authority: "candidate", scope: "task",\n'
            + '    source: "inf1", content: "inferred" },\n'
            + "]);\n"
            + "const result = applyDelta(cache, delta);\n"
            + "const active = result.rules.filter(\n"
            + '  r => r.key === "k1" && r.status === "active"\n'
            + ");\n"
            + "console.log(JSON.stringify({ count: active.length }));\n"
        )
        data = _run_bun(code)
        self.assertEqual(
            data.get("count"), 0,
            "inferred (candidate) rule must never be active",
        )


# --------------------------------------------------------------------
# AC 5-7: Extension integration (index.ts)
# --------------------------------------------------------------------


class ExtensionIntegrationTests(unittest.TestCase):
    """Source-text assertions on index.ts: rule cache XML injection,
    manifest, session restore, delta application, validation guards."""

    def setUp(self):
        self.text = (
            EXTENSION_FILE.read_text(encoding="utf-8")
            if EXTENSION_FILE.is_file()
            else ""
        )

    # AC 6: rule_cache visible XML in teamflow_context
    def test_before_agent_start_includes_rule_cache_xml(self):
        self.assertIn(
            "rule_cache",
            self.text,
            "before_agent_start must include <rule_cache> XML in the "
            "teamflow_context message",
        )

    def test_context_manifest_includes_rule_cache_source(self):
        self.assertTrue(
            'kind="rule_cache"' in self.text
            or "rule_cache" in self.text,
            "context_manifest must include a rule_cache source",
        )

    def test_rule_cache_not_in_system_prompt(self):
        # before_agent_start must return a visible message, not a
        # systemPrompt field carrying rule content.
        self.assertNotIn(
            "systemPrompt:",
            self.text,
            "rule cache must NOT be hidden in a systemPrompt field",
        )

    # AC 5: finish=length / failure / structural validation guards
    def test_checks_finish_length(self):
        self.assertIn(
            "finish",
            self.text.lower(),
            "agent_settled must check for finish=length and reject delta",
        )
        self.assertTrue(
            "length" in self.text.lower(),
            "agent_settled must reference 'length' truncation",
        )

    def test_checks_result_status_pass(self):
        self.assertIn(
            "PASS",
            self.text,
            "agent_settled must check teamflow_result status — "
            "only PASS applies the delta",
        )

    def test_validates_delta_structure(self):
        self.assertTrue(
            "validateDelta" in self.text
            or "validate" in self.text.lower(),
            "a validateDelta() or equivalent guard must exist for "
            "structural completeness checking",
        )

    # AC 7: session restore and persistence
    def test_session_start_restores_rule_cache(self):
        self.assertTrue(
            "rule" in self.text.lower()
            and "restore" in self.text.lower(),
            "session_start must restore the rule cache from prior "
            "session entries",
        )

    def test_agent_settled_applies_rule_cache_delta(self):
        self.assertIn(
            "delta",
            self.text.lower(),
            "agent_settled must apply/persist the rule cache delta",
        )

    def test_canonical_hash_on_restore(self):
        self.assertTrue(
            any(
                token in self.text
                for token in (
                    "verify", "contentHash", "content_hash",
                    "sha256",
                )
            ),
            "restore must verify the canonical content hash",
        )

    def test_rule_cache_not_in_basic_memory_knowledge(self):
        self.assertNotIn(
            "knowledge/",
            self.text,
            "rule cache persistence must NOT use the Basic Memory "
            "knowledge/ directory",
        )

    def test_rule_cache_entry_type_defined(self):
        self.assertTrue(
            "teamflow:rule_cache" in self.text,
            "a teamflow:rule_cache or teamflow:rule_cache_applied "
            "custom entry type must be defined for persistence/receipt",
        )


# --------------------------------------------------------------------
# AC 6: Phase D regression — invariants must still hold
# --------------------------------------------------------------------


class PhaseDRegressionTests(unittest.TestCase):
    """Phase D invariants must not be broken by Phase E integration."""

    def setUp(self):
        self.text = (
            EXTENSION_FILE.read_text(encoding="utf-8")
            if EXTENSION_FILE.is_file()
            else ""
        )

    def test_context_hook_still_registered(self):
        pattern = re.compile(
            r"""on\s*\(\s*['"]context['"]""", re.MULTILINE
        )
        self.assertTrue(
            bool(pattern.search(self.text)),
            "context hook must still be registered (Phase D)",
        )

    def test_session_before_compact_still_cancels(self):
        pattern = re.compile(
            r"""on\s*\(\s*['"]session_before_compact['"]""",
            re.MULTILINE,
        )
        self.assertTrue(
            bool(pattern.search(self.text)),
            "session_before_compact hook must still be registered",
        )
        self.assertTrue(
            bool(re.search(r"""cancel\s*:\s*true""", self.text)),
            "session_before_compact must still return { cancel: true }",
        )

    def test_context_budget_exceeded_still_referenced(self):
        self.assertIn(
            "CONTEXT_BUDGET_EXCEEDED",
            self.text,
            "CONTEXT_BUDGET_EXCEEDED must still be referenced",
        )

    def test_projection_keeps_latest_context_message(self):
        self.assertTrue(
            any(
                token in self.text
                for token in (
                    "latest", "findLast", "keepLast",
                    "lastContext", "retain",
                )
            ),
            "context projection must still keep the latest "
            "teamflow:context message",
        )


# --------------------------------------------------------------------
# AC 5: validateDelta source-text (rule-cache.ts or rule-cache-reducer.ts)
# --------------------------------------------------------------------


class ValidateDeltaSourceTests(unittest.TestCase):
    """A validateDelta or equivalent guard must exist in the reducer or
    rule-cache module."""

    def test_validate_delta_exists(self):
        reducer_text = (
            RULE_CACHE_REDUCER_FILE.read_text(encoding="utf-8")
            if RULE_CACHE_REDUCER_FILE.is_file()
            else ""
        )
        cache_text = (
            RULE_CACHE_FILE.read_text(encoding="utf-8")
            if RULE_CACHE_FILE.is_file()
            else ""
        )
        combined = reducer_text + "\n" + cache_text
        self.assertTrue(
            "validateDelta" in combined
            or "validate" in combined.lower(),
            "validateDelta() or equivalent must exist in "
            "rule-cache.ts or rule-cache-reducer.ts",
        )

    def test_reducer_file_exists(self):
        self.assertTrue(
            RULE_CACHE_REDUCER_FILE.is_file(),
            "rule-cache-reducer.ts must exist under memory-context/",
        )

    def test_reducer_exports_apply_delta(self):
        reducer_text = (
            RULE_CACHE_REDUCER_FILE.read_text(encoding="utf-8")
            if RULE_CACHE_REDUCER_FILE.is_file()
            else ""
        )
        self.assertTrue(
            "applyDelta" in reducer_text or "reduce" in reducer_text,
            "rule-cache-reducer.ts must export applyDelta (or reduce)",
        )


# --------------------------------------------------------------------
# AC 8-9: Infrastructure (doctor, init-project, README, design doc)
# --------------------------------------------------------------------


class InfrastructureTests(unittest.TestCase):
    """doctor.sh, init-project.sh, README.md, and design doc must
    reference Phase E rule cache."""

    def test_doctor_checks_rule_cache(self):
        doctor = DOCTOR_FILE.read_text(encoding="utf-8")
        for module in ("rule-cache",):
            with self.subTest(module=module):
                self.assertIn(
                    module,
                    doctor,
                    f"doctor.sh must check for {module}",
                )

    def test_doctor_checks_rule_cache_reducer(self):
        doctor = DOCTOR_FILE.read_text(encoding="utf-8")
        self.assertIn(
            "rule-cache-reducer",
            doctor,
            "doctor.sh must check for rule-cache-reducer",
        )

    def test_init_project_ships_rule_cache(self):
        init_script = INIT_FILE.read_text(encoding="utf-8")
        self.assertIn(
            "rule-cache",
            init_script,
            "init-project.sh must ship rule-cache.ts",
        )

    def test_init_project_ships_rule_cache_reducer(self):
        init_script = INIT_FILE.read_text(encoding="utf-8")
        self.assertIn(
            "rule-cache-reducer",
            init_script,
            "init-project.sh must ship rule-cache-reducer.ts",
        )

    def test_readme_documents_phase_e(self):
        readme = README_FILE.read_text(encoding="utf-8")
        self.assertTrue(
            any(
                token in readme
                for token in (
                    "rule cache", "rule_cache", "准则 cache",
                    "准则cache", "Phase E", "阶段 E",
                )
            ),
            "README.md must document Phase E rule cache feature",
        )

    def test_design_doc_marks_phase_e_implemented(self):
        design = DESIGN_DOC.read_text(encoding="utf-8")
        self.assertIn(
            "准则",
            design,
            "design doc must contain the rule cache (准则 cache) section",
        )
        self.assertTrue(
            any(
                token in design
                for token in (
                    "已实现", "rule_cache", "rule cache",
                    "memory_delta",
                )
            ),
            "design doc must reference Phase E implementation status",
        )


if __name__ == "__main__":
    unittest.main()
