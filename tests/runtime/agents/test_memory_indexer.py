"""Requirement tests for the ``memory-indexer`` agent definition.

Extracted from the retired ``.teamflow/tests/test_memory_context_phase_f.py``:
the other Phase F classes were source-text checks of pure-logic modules now
covered by bun tests, while this class pins the ``memory-indexer.md`` agent
file, which bun cannot test.

All paths are relative to the repository root
``ROOT = Path(__file__).resolve().parents[3]``.
"""

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]

INDEXER_AGENT_FILE = ROOT / ".teamflow" / "agents" / "memory-indexer.md"


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


if __name__ == "__main__":
    unittest.main()
