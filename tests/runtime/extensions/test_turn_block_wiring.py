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

  * ``AgentSettledWiringTests`` — source-text assertions on index.ts for
    Phase B persistence wiring (imports, writeTurn, failure reporting,
    store interface usage).

The TurnBlock content model itself (messages serialization, round-trip,
hash coverage, empty messages, secret redaction) is covered behaviorally
by the bun tests in ``.teamflow/extensions/memory-context/turn-block.test.ts``.

All paths are relative to the repository root
``ROOT = Path(__file__).resolve().parents[3]``.
"""

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]

EXTENSION_FILE = (
    ROOT / ".teamflow" / "extensions" / "memory-context" / "index.ts"
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
