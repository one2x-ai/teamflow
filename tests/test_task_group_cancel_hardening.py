"""Requirement tests for run-id ``pi-task-group-cancel-hardening-20260727``.

Pin four cancellation/orphan hardening defects and one preservation contract
in ``.teamflow/extensions/teamflow-task/index.ts``:

D1 — SIGKILL fallback must check actual child liveness (``proc.exitCode`` /
     ``proc.signalCode`` or a named closed/exited flag), not ``proc.killed``
     (which means "signal sent", not "process exited").

D2 — The abort ``addEventListener("abort", ...)`` listener and the
     ``setTimeout(...)`` SIGKILL timer must be removed/cleared when the child
     process closes normally or errors (``removeEventListener``,
     ``clearTimeout``).

D3 — No abort-listener accumulation: with ``task_group`` sharing one
     ``AbortSignal`` across many children, every ``addEventListener("abort")``
     must have a matching ``removeEventListener``.

D4 — ``max_concurrency`` must be clamped to a hard upper bound of 8 using
     ``Math.min``, and the ``TaskGroupParams`` description must document the
     ceiling.

D5 — Existing ``task_group`` behavior is preserved: tool registration,
     ``Promise.all`` fan-in, bounded worker pool, ``results[index]``
     input-order assignment, and ``wasAborted`` cancellation check.

All assertions are text-based against the extension source file, mirroring the
convention in ``tests/test_pi_task_extension.py``.
"""

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
EXTENSION_FILE = ROOT / ".teamflow" / "extensions" / "teamflow-task" / "index.ts"


class CancelHardeningTests(unittest.TestCase):
    """D1-D4: cancellation/orphan hardening contracts."""

    def setUp(self):
        self.text = EXTENSION_FILE.read_text(encoding="utf-8")

    # -- D1: SIGKILL liveness check ------------------------------------------

    def test_sigkill_check_uses_exit_status_not_proc_killed(self):
        """D1: SIGKILL fallback must not be guarded by proc.killed."""
        # proc.killed means "signal sent", not "process exited". No single
        # line should combine proc.killed with a SIGKILL guard.
        offending = [
            line
            for line in self.text.splitlines()
            if re.search(r"proc\.killed", line) and re.search(r"SIGKILL", line)
        ]
        self.assertFalse(
            offending,
            "SIGKILL fallback must not be guarded by proc.killed "
            "(proc.killed means 'signal sent', not 'process exited'): "
            f"{offending}",
        )
        # The SIGKILL fallback must reference a real exit-status / liveness
        # indicator: proc.exitCode or proc.signalCode.
        self.assertTrue(
            re.search(r"proc\.(exitCode|signalCode)", self.text),
            "SIGKILL liveness check must reference proc.exitCode or "
            "proc.signalCode instead of proc.killed",
        )

    # -- D2: abort listener and SIGKILL timer cleanup ------------------------

    def test_abort_listener_removed_on_close(self):
        """D2: signal.removeEventListener must exist for abort listener cleanup."""
        self.assertIn(
            "removeEventListener",
            self.text,
            "abort listener must be removed on child close/error to prevent leaks",
        )

    def test_kill_timer_cleared_on_close(self):
        """D2: clearTimeout must exist for SIGKILL timer cleanup."""
        self.assertIn(
            "clearTimeout",
            self.text,
            "SIGKILL timer must be cleared on child close/error",
        )

    # -- D3: no abort-listener accumulation ----------------------------------

    def test_abort_listener_cleanup_matches_registrations(self):
        """D3: every addEventListener('abort') needs a matching removeEventListener."""
        add_count = len(
            re.findall(r'addEventListener\s*\(\s*["\']abort["\']', self.text)
        )
        remove_count = len(re.findall(r"removeEventListener", self.text))
        self.assertGreaterEqual(
            remove_count,
            add_count,
            f"abort listener accumulation: {add_count} addEventListener('abort') "
            f"vs {remove_count} removeEventListener — every abort listener "
            f"must be cleaned up on close/error",
        )

    # -- D4: max_concurrency hard ceiling of 8 -------------------------------

    def test_concurrency_ceiling_is_eight(self):
        """D4: the concurrency hard ceiling must be 8."""
        self.assertTrue(
            re.search(r"MAX_CONCURRENCY\s*=\s*8", self.text)
            or re.search(r"Math\.min\s*\(\s*8\b", self.text)
            or re.search(r"Math\.min\s*\(\s*[^,]*,\s*8\s*\)", self.text),
            "concurrency ceiling must be 8 — define MAX_CONCURRENCY = 8 or "
            "use 8 inline with Math.min",
        )

    def test_max_concurrency_clamped_with_math_min(self):
        """D4: max_concurrency must be clamped with Math.min applying the ceiling."""
        self.assertTrue(
            re.search(r"Math\.min\s*\(\s*(?:MAX_CONCURRENCY|8)\b", self.text)
            or re.search(
                r"Math\.min\s*\([^)]*?(?:MAX_CONCURRENCY|\b8\b)\s*[,)]",
                self.text,
            ),
            "max_concurrency must be clamped with Math.min using "
            "MAX_CONCURRENCY or 8 as the ceiling",
        )

    def test_max_concurrency_description_documents_upper_bound(self):
        """D4: the max_concurrency param description must document the ceiling."""
        desc_match = re.search(
            r'max_concurrency:\s*Type\.Optional\(\s*'
            r'Type\.Number\(\s*\{\s*description:\s*"([^"]+)"',
            self.text,
        )
        self.assertIsNotNone(
            desc_match,
            "max_concurrency must have a description string in TaskGroupParams",
        )
        desc = desc_match.group(1)
        self.assertIn(
            "8",
            desc,
            f"max_concurrency description must document the upper bound of 8: "
            f"{desc!r}",
        )


class TaskGroupPreservationTests(unittest.TestCase):
    """D5: existing task_group behavior must be preserved (regression contracts)."""

    def setUp(self):
        self.text = EXTENSION_FILE.read_text(encoding="utf-8")

    def test_task_group_tool_registered(self):
        self.assertIn('"task_group"', self.text)

    def test_promise_all_used_for_fan_in(self):
        self.assertIn("Promise.all", self.text)

    def test_worker_pool_pattern_present(self):
        self.assertTrue(
            re.search(r"\bworker\b", self.text, re.IGNORECASE),
            "task_group must use a bounded worker pool pattern",
        )

    def test_results_assigned_by_index(self):
        self.assertRegex(
            self.text,
            r"results\s*\[",
            "results must be assigned by index for input-order preservation",
        )

    def test_was_aborted_cancellation_check(self):
        self.assertIn("wasAborted", self.text)


if __name__ == "__main__":
    unittest.main()
