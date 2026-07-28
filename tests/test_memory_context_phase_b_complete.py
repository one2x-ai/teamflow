"""Requirement tests for run-id ``memory-context-phase-b-complete-20260728``.

These tests pin the Phase B COMPLETION contract
(docs/teamflow-memory-context-design.md §8, §11.1, §12, §13, §19, §20):

Phase B introduced dormant modules (turn-block.ts, cold-memory-store.ts,
file-cold-store.ts) with metadata-only TurnBlocks. Phase B completion
requires:

1. TurnBlock carries full message/tool-call/tool-result content — not
   just metadata attributes (§11.1 ``<messages>`` body).
2. ``agent_settled`` builds and persists a complete TurnBlock via the
   replaceable ``ColdMemoryStore`` (§8, §12, §13).
3. Write failures are reported as ``MEMORY_PERSISTENCE_FAILED``, not
   faked (§19.6, §20).
4. ``redactSecrets`` filters known secret patterns before persistence
   (§19.1–§19.2).

Test sections:

  * ``TurnBlockContentTests`` — bun behavioral tests for the content model
    (messages serialization, round-trip, hash coverage, empty messages,
    secret redaction).
  * ``AgentSettledWiringTests`` — source-text assertions on index.ts for
    Phase B persistence wiring (imports, writeTurn, failure reporting,
    store interface usage).

All paths are relative to the repository root
``ROOT = Path(__file__).resolve().parents[1]``.
"""

import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]

EXTENSION_FILE = (
    ROOT / ".teamflow" / "extensions" / "memory-context" / "index.ts"
)
TURN_BLOCK_ABS = str(
    ROOT / ".teamflow" / "extensions" / "memory-context" / "turn-block.ts"
)

# Shared metadata block for bun test TurnBlock construction.
_META = (
    "  version: 1, id: \"turn-1\", sequence: 1, "
    "previous: null,\n"
    "  repository: \"repo\", taskId: \"task-1\", "
    "sessionId: \"sess-1\",\n"
    "  agent: \"planner\", "
    "startedAt: \"2025-01-01T00:00:00Z\",\n"
    "  settledAt: \"2025-01-01T00:01:00Z\", "
    "contentHash: \"\",\n"
)


# --------------------------------------------------------------------
# TurnBlock content model — bun behavioral tests
# --------------------------------------------------------------------


class TurnBlockContentTests(unittest.TestCase):
    """Phase B completion: TurnBlock content model via bun."""

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

    # (a) Messages serialize to <messages> body -------------------------

    def test_messages_serialize_to_messages_body(self):
        data = self._run_bun_test(
            'import { serialize } from "' + TURN_BLOCK_ABS + "\";\n"
            + "const block = {\n"
            + _META
            + "  messages: [\n"
            + "    { id: \"user-1\", role: \"user\","
            + " text: \"Hello world\" },\n"
            + "    { id: \"assistant-1\", role: \"assistant\","
            + " text: \"I will help\",\n"
            + "      toolCalls: [{ id: \"call-1\","
            + " name: \"read_file\","
            + " arguments: \"path=/foo\" }] },\n"
            + "    { id: \"result-1\", role: \"toolResult\","
            + " text: \"file content\",\n"
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
                self.assertIn(
                    required,
                    xml,
                    f"serialized XML must contain {required!r}",
                )

    # (b) Lossless round-trip with messages ------------------------------

    def test_roundtrip_preserves_messages(self):
        data = self._run_bun_test(
            'import { serialize, deserialize } from "'
            + TURN_BLOCK_ABS + "\";\n"
            + "const block = {\n"
            + _META
            + "  messages: [\n"
            + "    { id: \"user-1\", role: \"user\","
            + " text: \"Hello world\" },\n"
            + "    { id: \"assistant-1\", role: \"assistant\","
            + " text: \"I will help\",\n"
            + "      toolCalls: [{ id: \"call-1\","
            + " name: \"read_file\","
            + " arguments: \"path=/foo\" }] },\n"
            + "    { id: \"result-1\", role: \"toolResult\","
            + " text: \"file content\",\n"
            + "      callId: \"call-1\", status: \"ok\" },\n"
            + "  ],\n"
            + "};\n"
            + "const xml = serialize(block);\n"
            + "const restored = deserialize(xml);\n"
            + "console.log(JSON.stringify("
            + "{ messages: restored.messages }));\n"
        )
        messages = data.get("messages", [])
        self.assertEqual(len(messages), 3)

        # User message
        self.assertEqual(messages[0]["id"], "user-1")
        self.assertEqual(messages[0]["role"], "user")
        self.assertEqual(messages[0]["text"], "Hello world")

        # Assistant message with tool call
        self.assertEqual(messages[1]["id"], "assistant-1")
        self.assertEqual(messages[1]["role"], "assistant")
        self.assertEqual(messages[1]["text"], "I will help")
        tc = messages[1]["toolCalls"][0]
        self.assertEqual(tc["id"], "call-1")
        self.assertEqual(tc["name"], "read_file")
        self.assertEqual(tc["arguments"], "path=/foo")

        # Tool result message
        self.assertEqual(messages[2]["id"], "result-1")
        self.assertEqual(messages[2]["role"], "toolResult")
        self.assertEqual(messages[2]["text"], "file content")
        self.assertEqual(messages[2]["callId"], "call-1")
        self.assertEqual(messages[2]["status"], "ok")

    # (c) Hash covers messages -------------------------------------------

    def test_hash_differs_with_different_messages(self):
        code = (
            'import { computeContentHash } from "'
            + TURN_BLOCK_ABS + "\";\n"
            + "const base = {\n"
            + _META
            + "};\n"
            + "const a = { ...base, messages: "
            + "[{ id: \"u1\", role: \"user\", text: \"alpha\" }] };\n"
            + "const b = { ...base, messages: "
            + "[{ id: \"u1\", role: \"user\", text: \"beta\" }] };\n"
            + "const a2 = { ...base, messages: "
            + "[{ id: \"u1\", role: \"user\", text: \"alpha\" }] };\n"
            + "console.log(JSON.stringify({\n"
            + "  ha: computeContentHash(a),\n"
            + "  hb: computeContentHash(b),\n"
            + "  ha2: computeContentHash(a2),\n"
            + "}));\n"
        )
        data = self._run_bun_test(code)
        ha = data.get("ha", "")
        hb = data.get("hb", "")
        ha2 = data.get("ha2", "")
        # Different messages → different hash
        self.assertNotEqual(
            ha, hb,
            "blocks with different message content must have "
            "different hashes",
        )
        # Same messages → identical hash
        self.assertEqual(ha, ha2)
        # Hash format
        for label, h in (("ha", ha), ("hb", hb)):
            with self.subTest(hash=label):
                self.assertTrue(
                    h.startswith("sha256:"),
                    f"hash must start with sha256:: {h!r}",
                )
                hex_part = h[len("sha256:"):]
                self.assertEqual(len(hex_part), 64)
                self.assertTrue(
                    all(c in "0123456789abcdef" for c in hex_part),
                    f"hex digest must be lowercase hex: {hex_part!r}",
                )

    # (d) Empty messages array backward compatibility --------------------

    def test_empty_messages_roundtrip(self):
        data = self._run_bun_test(
            'import { serialize, deserialize } from "'
            + TURN_BLOCK_ABS + "\";\n"
            + "const block = {\n"
            + _META
            + "  messages: [],\n"
            + "};\n"
            + "const xml = serialize(block);\n"
            + "const restored = deserialize(xml);\n"
            + "console.log(JSON.stringify("
            + "{ xml, messages: restored.messages }));\n"
        )
        xml = data.get("xml", "")
        messages = data.get("messages")
        self.assertIn("<teamflow_turn", xml)
        # Empty messages should round-trip as empty array or absent
        self.assertTrue(
            messages is None or messages == [],
            f"empty messages must round-trip cleanly: {messages!r}",
        )

    def test_no_messages_field_roundtrip(self):
        data = self._run_bun_test(
            'import { serialize, deserialize } from "'
            + TURN_BLOCK_ABS + "\";\n"
            + "const block = {\n"
            + _META
            + "};\n"
            + "const xml = serialize(block);\n"
            + "const restored = deserialize(xml);\n"
            + "console.log(JSON.stringify({ restored }));\n"
        )
        restored = data.get("restored", {})
        # Metadata-only TurnBlock (no messages) must still round-trip
        self.assertEqual(restored.get("version"), 1)
        self.assertEqual(restored.get("id"), "turn-1")

    # (e) Secret redaction -----------------------------------------------

    def test_redact_secrets_replaces_api_key(self):
        data = self._run_bun_test(
            'import { redactSecrets } from "'
            + TURN_BLOCK_ABS + "\";\n"
            + "const input = "
            + "\"My key is sk-abcdefghijklmnopqrstuvwxyz1234567890\";\n"
            + "const output = redactSecrets(input);\n"
            + "console.log(JSON.stringify({ output }));\n"
        )
        output = data.get("output", "")
        self.assertNotIn(
            "sk-abcdefghijklmnopqrstuvwxyz1234567890", output
        )
        self.assertIn("[REDACTED]", output)

    def test_redact_secrets_replaces_bearer_token(self):
        data = self._run_bun_test(
            'import { redactSecrets } from "'
            + TURN_BLOCK_ABS + "\";\n"
            + "const input = "
            + "\"Authorization: Bearer "
            + "eyJhbGciOiJIUzI1NiJ9.payload.sig\";\n"
            + "const output = redactSecrets(input);\n"
            + "console.log(JSON.stringify({ output }));\n"
        )
        output = data.get("output", "")
        self.assertNotIn(
            "eyJhbGciOiJIUzI1NiJ9.payload.sig", output
        )
        self.assertIn("[REDACTED]", output)

    def test_redact_secrets_replaces_key_value_pairs(self):
        data = self._run_bun_test(
            'import { redactSecrets } from "'
            + TURN_BLOCK_ABS + "\";\n"
            + "const input = "
            + "\"password=secret123 api_key: ABCDEF "
            + "SECRET=mysecret\";\n"
            + "const output = redactSecrets(input);\n"
            + "console.log(JSON.stringify({ output }));\n"
        )
        output = data.get("output", "")
        for secret in ("secret123", "ABCDEF", "mysecret"):
            with self.subTest(secret=secret):
                self.assertNotIn(secret, output)
        self.assertIn("[REDACTED]", output)

    def test_redact_secrets_replaces_private_key_block(self):
        data = self._run_bun_test(
            'import { redactSecrets } from "'
            + TURN_BLOCK_ABS + "\";\n"
            + "const input = "
            + "\"-----BEGIN RSA PRIVATE KEY-----\\n"
            + "MIIEpAIBAAKCAQEA...\\n"
            + "-----END RSA PRIVATE KEY-----\";\n"
            + "const output = redactSecrets(input);\n"
            + "console.log(JSON.stringify({ output }));\n"
        )
        output = data.get("output", "")
        self.assertNotIn("MIIEpAIBAAKCAQEA", output)
        self.assertIn("[REDACTED]", output)

    def test_redact_secrets_preserves_normal_text(self):
        data = self._run_bun_test(
            'import { redactSecrets } from "'
            + TURN_BLOCK_ABS + "\";\n"
            + "const input = "
            + "\"This is a normal message without secrets.\";\n"
            + "const output = redactSecrets(input);\n"
            + "console.log(JSON.stringify({ output }));\n"
        )
        output = data.get("output", "")
        self.assertEqual(
            output, "This is a normal message without secrets."
        )


# --------------------------------------------------------------------
# agent_settled persistence wiring — source-text assertions on index.ts
# --------------------------------------------------------------------


class AgentSettledWiringTests(unittest.TestCase):
    """Phase B completion: agent_settled persists complete turns."""

    def setUp(self):
        self.text = (
            EXTENSION_FILE.read_text(encoding="utf-8")
            if EXTENSION_FILE.is_file()
            else ""
        )

    # (f) imports from ./turn-block --------------------------------------

    def test_imports_turn_block(self):
        self.assertIn(
            "./turn-block",
            self.text,
            "index.ts must import from ./turn-block",
        )

    # (g) imports from cold-memory modules -------------------------------

    def test_imports_cold_memory_modules(self):
        self.assertTrue(
            "./cold-memory-store" in self.text
            or "./file-cold-store" in self.text,
            "index.ts must import from ./cold-memory-store "
            "or ./file-cold-store",
        )

    # (h) references writeTurn -------------------------------------------

    def test_references_write_turn(self):
        self.assertIn(
            "writeTurn",
            self.text,
            "index.ts must call writeTurn to persist turns",
        )

    # (i) references MEMORY_PERSISTENCE_FAILED ---------------------------

    def test_references_memory_persistence_failed(self):
        self.assertIn(
            "MEMORY_PERSISTENCE_FAILED",
            self.text,
            "index.ts must report MEMORY_PERSISTENCE_FAILED on "
            "write failure",
        )

    # (j) agent_settled still registered ---------------------------------

    def test_references_agent_settled(self):
        self.assertIn("agent_settled", self.text)

    # (k) builds TurnBlock with messages from session entries ------------

    def test_builds_turn_from_session_entries(self):
        self.assertIn(
            "getEntries",
            self.text,
            "index.ts must read session entries to build TurnBlock",
        )

    def test_checks_user_role(self):
        self.assertTrue(
            '"user"' in self.text or "'user'" in self.text,
            "index.ts must check for 'user' role",
        )

    def test_checks_assistant_role(self):
        self.assertTrue(
            '"assistant"' in self.text or "'assistant'" in self.text,
            "index.ts must check for 'assistant' role",
        )

    def test_handles_tool_result_role(self):
        self.assertTrue(
            '"toolResult"' in self.text or "'toolResult'" in self.text,
            "index.ts must handle 'toolResult' role",
        )

    # (l) persists persistence status via appendEntry --------------------

    def test_appends_persistence_status(self):
        self.assertIn("appendEntry", self.text)
        self.assertTrue(
            "teamflow:cold_memory_persistence" in self.text
            or "teamflow:cold_memory" in self.text,
            "index.ts must appendEntry with a cold_memory "
            "persistence type",
        )

    # (m) uses ColdMemoryStore interface (replaceable) ------------------

    def test_uses_cold_memory_store_interface(self):
        self.assertIn(
            "ColdMemoryStore",
            self.text,
            "index.ts must use the ColdMemoryStore interface type",
        )


if __name__ == "__main__":
    unittest.main()
