"""Requirement tests for Phase D: runtime cold/hot context projection
and no-compact behavior.

Phase D (docs/teamflow-memory-context-design.md S8 Pi Hook Design,
S15 热区逐出, S16 预算与失败):

- The extension registers a ``context`` hook that performs hot-zone
  projection: it receives a deep copy of session messages and returns a
  replacement ``{ messages: [...] }`` array.  The projection keeps the
  latest ``teamflow:context`` message, the active turn, and the latest
  completed turn (TurnBlock).  Evicted turns are referenced via the cold
  store (FileColdStore) rather than being summarized.  Tool call /
  tool result causal pairs must not be split across the projection
  boundary.
- The extension registers ``session_before_compact`` and
  ``session_compact`` hooks.  ``session_before_compact`` returns
  ``{ cancel: true }`` for all reasons (manual, threshold, overflow),
  hard-cancelling compaction.  ``session_compact`` fires after
  compaction for invariant-violation detection.
- The extension references ``CONTEXT_BUDGET_EXCEEDED`` and contains
  budget-checking logic in the context/projection path.  When budget is
  exceeded, a structured failure receipt is produced via
  ``appendEntry`` with budget evidence.
- pi-runtime / config ensures compaction is disabled via settings.json
  or pi-runtime source.

All source-text assertions read EXTENSION_FILE
(``.teamflow/extensions/memory-context/index.ts``).  Tests are
deterministic and do not depend on network, providers, or credentials.
"""

import json
import os
import re
import shutil
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WRAPPER = ROOT / ".teamflow" / "bin" / "teamflow"
EXTENSION_FILE = (
    ROOT / ".teamflow" / "extensions" / "memory-context" / "index.ts"
)
README_FILE = ROOT / "README.md"
SETTINGS_FILE = ROOT / ".teamflow" / "settings.json"
PI_RUNTIME_FILE = ROOT / ".teamflow" / "bin" / "pi-runtime"


def _run(args, *, cwd=ROOT, env=None, timeout=30):
    return subprocess.run(
        args,
        cwd=str(cwd),
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
        stdin=subprocess.DEVNULL,
    )


# --------------------------------------------------------------------
# Context hook: hot-zone projection (AC 1-7)
# --------------------------------------------------------------------


class ContextHookTests(unittest.TestCase):
    """Phase D context hook contracts: projection, cold-store eviction,
    tool-pair integrity."""

    def setUp(self):
        self.text = (
            EXTENSION_FILE.read_text(encoding="utf-8")
            if EXTENSION_FILE.is_file()
            else ""
        )

    def test_extension_file_exists(self):
        self.assertTrue(
            EXTENSION_FILE.is_file(),
            ".teamflow/extensions/memory-context/index.ts must exist",
        )

    # AC 1: context hook registered
    def test_registers_context_hook(self):
        pattern = re.compile(
            r"""on\s*\(\s*['"]context['"]""",
            re.MULTILINE,
        )
        self.assertTrue(
            bool(pattern.search(self.text)),
            "Phase D extension must register a 'context' hook "
            "(pi.on('context', ...))",
        )

    # AC 2: context handler returns { messages: ... }
    def test_context_handler_returns_messages(self):
        self.assertTrue(
            "{ messages" in self.text or "messages:" in self.text,
            "context handler must return ContextEventResult with a "
            "'messages' field (e.g. return { messages: [...] })",
        )

    # AC 3: projection keeps latest teamflow:context message
    def test_projection_keeps_latest_context_message(self):
        self.assertTrue(
            any(
                token in self.text
                for token in (
                    "latest", "keepLast", "lastContext",
                    "findLast", "retain", "lastTeamflow",
                )
            ),
            "projection must keep/retain the latest "
            "teamflow:context message",
        )

    # AC 4: projection references active/current turn
    def test_projection_references_active_turn(self):
        self.assertTrue(
            any(
                token in self.text
                for token in (
                    "activeTurn", "currentTurn", "active turn",
                    "current turn", "activeturn", "currentturn",
                )
            ),
            "projection must reference the active/current turn",
        )

    # AC 5: projection references latest completed turn / TurnBlock
    def test_projection_references_completed_turn_block(self):
        self.assertTrue(
            any(
                token in self.text
                for token in (
                    "completedTurn", "latestTurn", "previousTurn",
                    "evictedTurn", "evicted", "lastTurn",
                    "priorTurn", "settledTurn",
                    "readTurn", "readByOffset",
                )
            ),
            "projection must reference the latest completed turn or "
            "read it from the cold store (readTurn/readByOffset)",
        )

    # AC 6: no summarization; cold store for evicted turns
    def test_projection_no_summaries_uses_cold_store(self):
        self.assertNotIn(
            "summarize",
            self.text.lower(),
            "projection must NOT generate summaries to replace "
            "evicted turns",
        )
        self.assertTrue(
            any(
                token in self.text
                for token in ("ColdStore", "coldStore", "cold-store", "cold")
            ),
            "projection should reference the cold store for evicted "
            "turns rather than summaries",
        )

    # AC 7: tool call/result pairs not split
    def test_projection_keeps_tool_pairs_together(self):
        self.assertTrue(
            any(
                token in self.text
                for token in (
                    "toolCallId", "tool_call_id",
                    "causal", "pairing", "pair",
                    "unmatched",
                )
            ),
            "projection must keep tool call / tool result causal "
            "pairs together (references toolCallId, causal pairing, "
            "or similar)",
        )


# --------------------------------------------------------------------
# Compact interception (AC 8-10)
# --------------------------------------------------------------------


class CompactInterceptionTests(unittest.TestCase):
    """Phase D compact interception: session_before_compact cancels all
    compaction; session_compact detects invariant violations."""

    def setUp(self):
        self.text = (
            EXTENSION_FILE.read_text(encoding="utf-8")
            if EXTENSION_FILE.is_file()
            else ""
        )

    # AC 8: session_before_compact hook registered
    def test_registers_session_before_compact_hook(self):
        pattern = re.compile(
            r"""on\s*\(\s*['"]session_before_compact['"]""",
            re.MULTILINE,
        )
        self.assertTrue(
            bool(pattern.search(self.text)),
            "Phase D extension must register a 'session_before_compact' "
            "hook",
        )

    # AC 9: session_before_compact returns { cancel: true }
    def test_session_before_compact_cancels_all_reasons(self):
        self.assertTrue(
            bool(
                re.search(
                    r"""cancel\s*:\s*true""",
                    self.text,
                )
            ),
            "session_before_compact must return { cancel: true } to "
            "hard-cancel compaction for all reasons (manual, "
            "threshold, overflow)",
        )

    # AC 10: session_compact hook registered
    def test_registers_session_compact_hook(self):
        pattern = re.compile(
            r"""on\s*\(\s*['"]session_compact['"]""",
            re.MULTILINE,
        )
        self.assertTrue(
            bool(pattern.search(self.text)),
            "Phase D extension must register a 'session_compact' hook "
            "for invariant-violation detection",
        )


# --------------------------------------------------------------------
# Budget / CONTEXT_BUDGET_EXCEEDED (AC 11-13)
# --------------------------------------------------------------------


class BudgetExceededTests(unittest.TestCase):
    """Phase D budget checking: CONTEXT_BUDGET_EXCEEDED receipt when
    context budget is exceeded in the projection path."""

    def setUp(self):
        self.text = (
            EXTENSION_FILE.read_text(encoding="utf-8")
            if EXTENSION_FILE.is_file()
            else ""
        )

    # AC 11: source references CONTEXT_BUDGET_EXCEEDED
    def test_references_context_budget_exceeded(self):
        self.assertIn(
            "CONTEXT_BUDGET_EXCEEDED",
            self.text,
            "extension must reference CONTEXT_BUDGET_EXCEEDED "
            "(as a constant, string, or entry type)",
        )

    # AC 12: budget-checking logic in projection path
    def test_budget_checking_in_projection_path(self):
        self.assertTrue(
            any(
                token in self.text.lower()
                for token in (
                    "limit", "exceeded", "remaining", "threshold",
                    "budgetestimate", "budgetlimit",
                )
            ),
            "context handler must contain budget-estimation logic "
            "(references a limit/threshold/remaining tokens)",
        )

    # AC 13: budget exceeded produces structured failure receipt
    def test_budget_exceeded_produces_receipt(self):
        self.assertIn("CONTEXT_BUDGET_EXCEEDED", self.text)
        self.assertIn("appendEntry", self.text)
        self.assertTrue(
            any(
                token in self.text.lower()
                for token in ("limit", "used", "remaining")
            ),
            "budget exceeded receipt must include evidence like "
            "limit/used/remaining tokens",
        )


# --------------------------------------------------------------------
# pi-runtime / config compaction disabling (AC 14)
# --------------------------------------------------------------------


class PiRuntimeConfigTests(unittest.TestCase):
    """pi-runtime / config must disable compaction via settings.json or
    pi-runtime source."""

    def test_compaction_disabled_mechanism(self):
        settings_ok = False
        if SETTINGS_FILE.is_file():
            try:
                settings = json.loads(
                    SETTINGS_FILE.read_text(encoding="utf-8")
                )
                compaction = settings.get("compaction")
                if isinstance(compaction, dict):
                    settings_ok = compaction.get("enabled") is False
            except (json.JSONDecodeError, AttributeError):
                pass

        pi_runtime_text = (
            PI_RUNTIME_FILE.read_text(encoding="utf-8")
            if PI_RUNTIME_FILE.is_file()
            else ""
        )
        pi_runtime_ok = "compaction" in pi_runtime_text.lower()

        self.assertTrue(
            settings_ok or pi_runtime_ok,
            "compaction must be disabled via .teamflow/settings.json "
            "(compaction.enabled=false) or pi-runtime must reference "
            "compaction disabling",
        )


# --------------------------------------------------------------------
# Extension loads provider-free (AC 15)
# --------------------------------------------------------------------


class ExtensionLoadingTests(unittest.TestCase):
    """The extension file must parse / load under pi without a provider."""

    def test_extension_loads_provider_free(self):
        if shutil.which("pi") is None:
            self.skipTest("pi not on PATH")
        env = os.environ.copy()
        env["TEAMFLOW_AGENT_ROLE"] = "planner"
        env["TEAMFLOW_AGENT_DEPTH"] = "0"
        completed = _run(
            [
                "pi",
                "--extension",
                str(EXTENSION_FILE),
                "--help",
            ],
            env=env,
            timeout=30,
        )
        self.assertEqual(
            completed.returncode,
            0,
            f"pi --extension ... --help exited {completed.returncode}: "
            f"stdout={completed.stdout!r} stderr={completed.stderr!r}",
        )


# --------------------------------------------------------------------
# README documentation (AC 16)
# --------------------------------------------------------------------


class ReadmePhaseDTests(unittest.TestCase):
    """README.md must document Phase D features."""

    def test_readme_documents_phase_d(self):
        readme = README_FILE.read_text(encoding="utf-8")
        self.assertTrue(
            any(
                token in readme
                for token in ("热区", "CONTEXT_BUDGET", "预算")
            )
            or any(
                token in readme.lower()
                for token in (
                    "hot-zone", "hot zone", "no-compact", "no compact",
                )
            ),
            "README must document Phase D features: hot-zone "
            "projection, no-compact, or budget",
        )


if __name__ == "__main__":
    unittest.main()
