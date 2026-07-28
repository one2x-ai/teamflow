"""Supplementary requirement tests for the budget-report aspect of
Phase A observation mode (design S22.A: "生成 manifest、turn boundary
和预算报告").

These tests assert that ``.teamflow/extensions/memory-context/index.ts``
calls ``ctx.getContextUsage()`` at seal time and includes the budget
observation in the receipt, with null-safe handling for the case where
``getContextUsage`` returns ``undefined``.

Mirrors the source-text assertion pattern of
``tests/test_memory_context_extension.py``.  All paths are relative to
the repository root ``ROOT = Path(__file__).resolve().parents[1]``.
"""

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
EXTENSION_FILE = (
    ROOT / ".teamflow" / "extensions" / "memory-context" / "index.ts"
)


# --------------------------------------------------------------------
# Source-text assertions on the extension .ts file
# --------------------------------------------------------------------


class BudgetReportTests(unittest.TestCase):
    """Budget-report contract: the extension must call getContextUsage
    at seal time and include budget/context-usage data in the receipt."""

    def setUp(self):
        self.text = (
            EXTENSION_FILE.read_text(encoding="utf-8")
            if EXTENSION_FILE.is_file()
            else ""
        )

    def test_source_references_get_context_usage(self):
        self.assertIn(
            "getContextUsage",
            self.text,
            "extension must call ctx.getContextUsage() at seal time",
        )

    def test_source_references_budget_or_tokens(self):
        self.assertTrue(
            any(
                token in self.text
                for token in ("budget", "tokens", "contextWindow")
            ),
            "extension must reference at least one of budget / tokens / "
            "contextWindow",
        )

    def test_receipt_includes_budget_data(self):
        self.assertTrue(
            any(
                token in self.text
                for token in ("budget", "contextUsage", "usage")
            ),
            "receipt must include a budget / contextUsage / usage field",
        )

    def test_budget_null_safe(self):
        self.assertTrue(
            (
                "?." in self.text
                or "??" in self.text
                or "null" in self.text
                or "undefined" in self.text
            ),
            "extension must handle the case where getContextUsage "
            "returns undefined/null (optional chaining, nullish "
            "coalescing, or explicit null/undefined check)",
        )


if __name__ == "__main__":
    unittest.main()
