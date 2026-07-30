"""Requirement tests for Phase G: Planning Feedback (规划反馈).

Phase G (docs/teamflow-memory-context-design.md §22.G, §16, §17)
connects budget failures to phase receipts, enables planner re-split
with lineage, and gates planning experience capture on verification
success.

This file tests ONLY the observable contracts of the not-yet-implemented
Phase G features.  All tests must FAIL on the current codebase (RED)
because the features do not exist yet.

Test approaches (mirrors Phase D / E / F conventions):

1. Behavioral tests on ``phase_state.py`` — run the script directly in
   an isolated temp directory (``cwd=tmpdir``) and parse JSON receipts
   from stdout.
2. Source-text assertions — read the TypeScript / Markdown source and
   assert required patterns exist via ``re.compile`` / ``in`` checks.

All tests are deterministic: no network, no providers, no credentials.
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
WRAPPER = ROOT / ".teamflow" / "bin" / "teamflow"
PHASE_STATE_FILE = (
    ROOT / ".teamflow" / "skills" / "plan-change" / "scripts"
    / "phase_state.py"
)
EXTENSION_FILE = (
    ROOT / ".teamflow" / "extensions" / "memory-context" / "index.ts"
)
DESIGN_DOC = ROOT / "docs" / "teamflow-memory-context-design.md"
README_FILE = ROOT / "README.md"


def _run(args, *, cwd=ROOT, env=None, timeout=30):
    """Run *args* with capture, DEVNULL stdin."""
    return subprocess.run(
        args,
        cwd=str(cwd),
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
        stdin=subprocess.DEVNULL,
    )


def _run_phase(args, *, cwd, timeout=30):
    """Run phase_state.py directly in *cwd*; return CompletedProcess."""
    return _run(
        ["python3", str(PHASE_STATE_FILE)] + list(args),
        cwd=cwd,
        timeout=timeout,
    )


# Session-leaking field names that must NEVER appear in phase receipts
# or planning-experience files (AC E).
_SENSITIVE_FIELDS = frozenset({
    "prompt", "reasoning", "session", "rawError",
    "apiKey", "secret", "content", "raw_response", "raw_log",
})


def _recursive_keys(obj):
    """Yield all key names in a nested dict/list structure."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield k
            yield from _recursive_keys(v)
    elif isinstance(obj, list):
        for item in obj:
            yield from _recursive_keys(item)


# --------------------------------------------------------------------
# AC A: Phase receipt budget failure (phase_state.py finish command)
# --------------------------------------------------------------------


class PhaseGBudgetReceiptTests(unittest.TestCase):
    """Tests for the ``finish`` subcommand budget-failure arguments.

    When ``--block-reason`` is present, the phase receipt JSON must
    contain a ``budget_failure`` object with all budget fields.
    When ``--block-reason`` is absent, no ``budget_failure`` key
    appears (backward compatibility).
    """

    def setUp(self):
        self._tmpdir = tempfile.mkdtemp(prefix="phaseg_budget_")

    def tearDown(self):
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def _start_and_finish(
        self, run_id, phase, owner, finish_args,
    ):
        """Start a phase then finish it with *finish_args*; return
        parsed JSON receipt from stdout."""
        _run_phase(
            [
                "start", "--run-id", run_id,
                "--phase", phase, "--owner", owner,
            ],
            cwd=self._tmpdir,
        )
        result = _run_phase(
            [
                "finish", "--run-id", run_id,
                "--status", "BLOCKED",
                "--summary", "Budget exceeded during context projection",
            ]
            + finish_args,
            cwd=self._tmpdir,
        )
        self.assertEqual(
            result.returncode, 0,
            f"phase finish exited {result.returncode}:\n"
            f"stdout={result.stdout}\nstderr={result.stderr}",
        )
        return json.loads(result.stdout)

    def test_finish_with_context_budget_exceeded(self):
        """finish with --block-reason CONTEXT_BUDGET_EXCEEDED must
        produce a budget_failure object in the receipt."""
        largest = json.dumps([
            {"kind": "project_rules", "ref": "AGENTS.md",
             "tokens": 15000},
        ])
        source_refs = json.dumps([
            {"kind": "rule_cache", "ref": "cache://adhoc",
             "hash": "sha256:abc"},
        ])
        receipt = self._start_and_finish(
            "run-ctx-1", "phase-1", "planner",
            [
                "--block-reason", "CONTEXT_BUDGET_EXCEEDED",
                "--budget-limit", "100000",
                "--budget-used", "120000",
                "--budget-remaining", "-20000",
                "--protected-component",
                "project_rules+rule_cache+latest_turn+active_turn",
                "--required-action", "REPLAN_AND_SPLIT",
                "--largest-sources", largest,
                "--source-refs", source_refs,
            ],
        )
        self.assertEqual(receipt["status"], "BLOCKED")
        self.assertIn("budget_failure", receipt)
        bf = receipt["budget_failure"]
        self.assertEqual(bf["reason"], "CONTEXT_BUDGET_EXCEEDED")
        self.assertEqual(bf["budget"]["limit"], 100000)
        self.assertEqual(bf["budget"]["used"], 120000)
        self.assertEqual(bf["budget"]["remaining"], -20000)
        self.assertIn("protected_component", bf)
        self.assertIn("project_rules", bf["protected_component"])
        self.assertEqual(bf["required_action"], "REPLAN_AND_SPLIT")
        self.assertEqual(len(bf["largest_sources"]), 1)
        self.assertEqual(bf["largest_sources"][0]["tokens"], 15000)
        self.assertEqual(len(bf["source_refs"]), 1)
        self.assertEqual(
            bf["source_refs"][0]["hash"], "sha256:abc",
        )

    def test_finish_with_recall_budget_exceeded(self):
        """finish with --block-reason RECALL_BUDGET_EXCEEDED must also
        produce a budget_failure object."""
        receipt = self._start_and_finish(
            "run-recall-1", "phase-1", "planner",
            [
                "--block-reason", "RECALL_BUDGET_EXCEEDED",
                "--budget-limit", "50000",
                "--budget-used", "60000",
                "--budget-remaining", "-10000",
                "--protected-component", "project_rules",
                "--required-action", "REPLAN_AND_SPLIT",
                "--largest-sources", "[]",
                "--source-refs", "[]",
            ],
        )
        self.assertEqual(receipt["status"], "BLOCKED")
        self.assertIn("budget_failure", receipt)
        bf = receipt["budget_failure"]
        self.assertEqual(bf["reason"], "RECALL_BUDGET_EXCEEDED")
        self.assertEqual(bf["budget"]["limit"], 50000)
        self.assertEqual(bf["budget"]["used"], 60000)
        self.assertEqual(bf["budget"]["remaining"], -10000)
        self.assertIn("protected_component", bf)
        self.assertIn("project_rules", bf["protected_component"])
        self.assertEqual(bf["required_action"], "REPLAN_AND_SPLIT")
        self.assertEqual(len(bf["largest_sources"]), 0)
        self.assertEqual(len(bf["source_refs"]), 0)

    def test_finish_without_block_reason_has_no_budget_failure(self):
        """Backward compatibility: finishing without --block-reason
        must NOT add a budget_failure key."""
        _run_phase(
            [
                "start", "--run-id", "run-normal",
                "--phase", "p1", "--owner", "planner",
            ],
            cwd=self._tmpdir,
        )
        result = _run_phase(
            [
                "finish", "--run-id", "run-normal",
                "--status", "PASS", "--summary", "Done",
            ],
            cwd=self._tmpdir,
        )
        self.assertEqual(result.returncode, 0)
        receipt = json.loads(result.stdout)
        self.assertNotIn("budget_failure", receipt)

    def test_budget_failure_no_sensitive_fields(self):
        """AC E: budget_failure must not contain raw session data
        fields."""
        receipt = self._start_and_finish(
            "run-safe", "phase-1", "planner",
            [
                "--block-reason", "CONTEXT_BUDGET_EXCEEDED",
                "--budget-limit", "100000",
                "--budget-used", "120000",
                "--budget-remaining", "-20000",
                "--protected-component", "project_rules",
                "--required-action", "REPLAN_AND_SPLIT",
                "--largest-sources", "[]",
                "--source-refs", "[]",
            ],
        )
        bf = receipt["budget_failure"]
        for key in _recursive_keys(bf):
            self.assertNotIn(
                key, _SENSITIVE_FIELDS,
                f"budget_failure must not contain sensitive "
                f"field '{key}'",
            )


# --------------------------------------------------------------------
# AC B: Phase receipt lineage (phase_state.py start command)
# --------------------------------------------------------------------


class PhaseGLineageTests(unittest.TestCase):
    """Tests for the ``start`` subcommand lineage arguments.

    When ``--parent-run-id`` / ``--parent-phase`` / ``--split-scope``
    are provided, the phase receipt must contain a ``lineage`` object.
    """

    def setUp(self):
        self._tmpdir = tempfile.mkdtemp(prefix="phaseg_lineage_")

    def tearDown(self):
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def test_start_with_lineage_args(self):
        """start with --parent-run-id / --parent-phase / --split-scope
        must add a lineage object to the receipt."""
        result = _run_phase(
            [
                "start",
                "--run-id", "child-run-1",
                "--phase", "implement",
                "--owner", "coder",
                "--parent-run-id", "parent-run-0",
                "--parent-phase", "plan",
                "--split-scope", "backend-api",
            ],
            cwd=self._tmpdir,
        )
        self.assertEqual(
            result.returncode, 0,
            f"phase start exited {result.returncode}:\n"
            f"stdout={result.stdout}\nstderr={result.stderr}",
        )
        receipt = json.loads(result.stdout)
        self.assertIn("lineage", receipt)
        self.assertEqual(
            receipt["lineage"]["parent_run_id"], "parent-run-0",
        )
        self.assertEqual(
            receipt["lineage"]["parent_phase"], "plan",
        )
        self.assertEqual(
            receipt["lineage"]["split_scope"], "backend-api",
        )

    def test_start_without_lineage_args(self):
        """Backward compatibility: start without lineage args must NOT
        add a lineage key."""
        result = _run_phase(
            [
                "start",
                "--run-id", "plain-run",
                "--phase", "implement",
                "--owner", "coder",
            ],
            cwd=self._tmpdir,
        )
        self.assertEqual(result.returncode, 0)
        receipt = json.loads(result.stdout)
        self.assertNotIn("lineage", receipt)


# --------------------------------------------------------------------
# AC C: Planning experience gating (phase_state.py
#        planning-experience subcommand)
# --------------------------------------------------------------------


class PhaseGPlanningExperienceTests(unittest.TestCase):
    """Tests for the ``planning-experience`` subcommand gating logic.

    The command reads all phase receipts under a run-id and gates the
    planning-experience file write on ALL phases being PASS.
    """

    def setUp(self):
        self._tmpdir = tempfile.mkdtemp(prefix="phaseg_exp_")

    def tearDown(self):
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def _create_parent_phase(self, parent_run_id, parent_phase):
        """Create a parent phase receipt with budget_failure metadata
        to serve as the origin of a re-split."""
        _run_phase(
            [
                "start", "--run-id", parent_run_id,
                "--phase", parent_phase, "--owner", "planner",
            ],
            cwd=self._tmpdir,
        )
        result = _run_phase(
            [
                "finish", "--run-id", parent_run_id,
                "--status", "BLOCKED",
                "--summary", "Context budget exceeded during planning",
                "--block-reason", "CONTEXT_BUDGET_EXCEEDED",
                "--budget-limit", "100000",
                "--budget-used", "120000",
                "--budget-remaining", "-20000",
                "--protected-component", "project_rules+rule_cache",
                "--required-action", "REPLAN_AND_SPLIT",
                "--largest-sources", "[]",
                "--source-refs", "[]",
            ],
            cwd=self._tmpdir,
        )
        self.assertEqual(
            result.returncode, 0,
            f"parent finish failed: {result.stderr}",
        )

    def _create_child(self, run_id, phase, status="PASS",
                      summary="Done"):
        """Start and finish a single child phase under *run_id*."""
        _run_phase(
            [
                "start", "--run-id", run_id,
                "--phase", phase, "--owner", "coder",
            ],
            cwd=self._tmpdir,
        )
        result = _run_phase(
            [
                "finish", "--run-id", run_id,
                "--status", status,
                "--summary", summary,
            ],
            cwd=self._tmpdir,
        )
        self.assertEqual(
            result.returncode, 0,
            f"child finish failed: {result.stderr}",
        )

    def _run_planning_experience(
        self, run_id,
        parent_run_id="parent-run", parent_phase="plan",
    ):
        """Run the planning-experience command; return CompletedProcess."""
        return _run_phase(
            [
                "planning-experience",
                "--run-id", run_id,
                "--parent-run-id", parent_run_id,
                "--parent-phase", parent_phase,
            ],
            cwd=self._tmpdir,
        )

    def _exp_path(self, run_id):
        return (
            Path(self._tmpdir) / ".teamflow" / "runs" / "code"
            / run_id / "planning-experience.json"
        )

    def test_all_children_pass_generates_experience(self):
        """All children PASS → experience file generated with correct
        content."""
        self._create_parent_phase("parent-run", "plan")
        self._create_child("split-run-1", "child-a", "PASS")
        self._create_child("split-run-1", "child-b", "PASS")

        result = self._run_planning_experience(
            "split-run-1", "parent-run", "plan",
        )
        self.assertEqual(
            result.returncode, 0,
            f"planning-experience exited {result.returncode}:\n"
            f"stdout={result.stdout}\nstderr={result.stderr}",
        )
        output = json.loads(result.stdout)
        self.assertEqual(output["status"], "generated")

        exp_path = self._exp_path("split-run-1")
        self.assertTrue(
            exp_path.is_file(),
            "planning-experience.json must exist",
        )
        exp = json.loads(
            exp_path.read_text(encoding="utf-8"),
        )
        self.assertIn("failure_mode", exp)
        self.assertIn("original_split", exp)
        self.assertIn("verified_new_split", exp)
        self.assertIn("evidence_receipt_refs", exp)
        self.assertIn("applicable_scope", exp)
        self.assertEqual(len(exp["verified_new_split"]), 2)
        # original_split must reference the parent run + phase
        orig = exp["original_split"]
        self.assertEqual(orig.get("run_id"), "parent-run")
        self.assertEqual(orig.get("phase"), "plan")

    def test_one_child_blocked_defers(self):
        """One child BLOCKED → deferred, no file written."""
        self._create_child("split-run-2", "child-a", "PASS")
        self._create_child("split-run-2", "child-b", "BLOCKED")

        result = self._run_planning_experience("split-run-2")
        self.assertEqual(result.returncode, 0)
        output = json.loads(result.stdout)
        self.assertEqual(output["status"], "deferred")

        self.assertFalse(
            self._exp_path("split-run-2").is_file(),
            "planning-experience.json must NOT exist when deferred",
        )

    def test_child_finish_length_defers(self):
        """AC 4: A child phase BLOCKED with summary containing
        'finish=length' (output truncation) must cause deferral."""
        self._create_child("split-run-6", "child-a", "PASS")
        self._create_child(
            "split-run-6", "child-b", "BLOCKED",
            "Output truncated: finish=length",
        )

        result = self._run_planning_experience("split-run-6")
        self.assertEqual(result.returncode, 0)
        output = json.loads(result.stdout)
        self.assertEqual(output["status"], "deferred")

        self.assertFalse(
            self._exp_path("split-run-6").is_file(),
        )

    def test_one_child_fail_defers(self):
        """One child FAIL → deferred, no file written."""
        self._create_child("split-run-3", "child-a", "PASS")
        self._create_child(
            "split-run-3", "child-b", "FAIL", "Build failed",
        )

        result = self._run_planning_experience("split-run-3")
        self.assertEqual(result.returncode, 0)
        output = json.loads(result.stdout)
        self.assertEqual(output["status"], "deferred")

        self.assertFalse(
            self._exp_path("split-run-3").is_file(),
        )

    def test_one_child_running_defers(self):
        """One child still RUNNING → deferred, no file written."""
        # Start child-a but never finish it (stays RUNNING)
        _run_phase(
            [
                "start", "--run-id", "split-run-4",
                "--phase", "child-a", "--owner", "coder",
            ],
            cwd=self._tmpdir,
        )
        self._create_child("split-run-4", "child-b", "PASS")

        result = self._run_planning_experience("split-run-4")
        self.assertEqual(result.returncode, 0)
        output = json.loads(result.stdout)
        self.assertEqual(output["status"], "deferred")

        self.assertFalse(
            self._exp_path("split-run-4").is_file(),
        )

    def test_no_phases_defers(self):
        """No child phases at all → deferred (nothing to verify)."""
        result = self._run_planning_experience("empty-run")
        self.assertEqual(result.returncode, 0)
        output = json.loads(result.stdout)
        self.assertEqual(output["status"], "deferred")

        self.assertFalse(
            self._exp_path("empty-run").is_file(),
        )

    def test_experience_no_sensitive_fields(self):
        """AC E: planning experience JSON must not contain raw prompts,
        secrets, or session content."""
        self._create_parent_phase("parent-run", "plan")
        self._create_child("split-run-5", "child-a", "PASS")
        self._create_child("split-run-5", "child-b", "PASS")

        result = self._run_planning_experience(
            "split-run-5", "parent-run", "plan",
        )
        self.assertEqual(result.returncode, 0)

        exp_path = self._exp_path("split-run-5")
        exp = json.loads(exp_path.read_text(encoding="utf-8"))
        for key in _recursive_keys(exp):
            self.assertNotIn(
                key, _SENSITIVE_FIELDS,
                f"planning-experience must not contain sensitive "
                f"field '{key}'",
            )


# --------------------------------------------------------------------
# AC D: Extension source checks (index.ts)
# --------------------------------------------------------------------


class PhaseGExtensionSourceTests(unittest.TestCase):
    """Source-text assertions on index.ts for Phase G extension changes.

    The extension must add RECALL_BUDGET_EXCEEDED, enrich the
    CONTEXT_BUDGET_EXCEEDED receipt with largest_sources /
    protected_component, and use REPLAN_AND_SPLIT for both budget
    types.
    """

    def setUp(self):
        self.text = (
            EXTENSION_FILE.read_text(encoding="utf-8")
            if EXTENSION_FILE.is_file()
            else ""
        )

    def test_recall_budget_exceeded_constant_exists(self):
        """index.ts must define a RECALL_BUDGET_EXCEEDED constant."""
        self.assertIn(
            "RECALL_BUDGET_EXCEEDED",
            self.text,
            "index.ts must define a RECALL_BUDGET_EXCEEDED constant",
        )

    def test_recall_budget_exceeded_entry_type_exists(self):
        """index.ts must define a recall budget exceeded entry type."""
        self.assertTrue(
            "recall_budget_exceeded" in self.text.lower(),
            "index.ts must define a recall budget exceeded entry type "
            "(e.g. teamflow:recall_budget_exceeded)",
        )

    def test_context_receipt_has_largest_sources(self):
        """CONTEXT_BUDGET_EXCEEDED receipt must include largest_sources
        or largestSources field."""
        self.assertTrue(
            "largestSources" in self.text
            or "largest_sources" in self.text,
            "CONTEXT_BUDGET_EXCEEDED receipt must include "
            "largestSources or largest_sources field",
        )

    def test_context_receipt_has_protected_component(self):
        """CONTEXT_BUDGET_EXCEEDED receipt must include
        protectedComponent or protected_component field."""
        self.assertTrue(
            "protectedComponent" in self.text
            or "protected_component" in self.text,
            "CONTEXT_BUDGET_EXCEEDED receipt must include "
            "protectedComponent or protected_component field",
        )

    def test_both_budget_types_use_replan_and_split(self):
        """Both CONTEXT_BUDGET_EXCEEDED and RECALL_BUDGET_EXCEEDED must
        use requiredAction: REPLAN_AND_SPLIT."""
        self.assertIn(
            "REPLAN_AND_SPLIT",
            self.text,
            "Both budget types must use requiredAction: "
            "REPLAN_AND_SPLIT",
        )

    def test_recall_budget_exceeded_is_used_not_just_defined(self):
        """RECALL_BUDGET_EXCEEDED must be referenced beyond its const
        declaration \u2014 it must appear more than once in index.ts (once
        for the constant definition, once for usage in the budget check
        logic or appendEntry)."""
        count = self.text.count("RECALL_BUDGET_EXCEEDED")
        self.assertGreaterEqual(
            count, 2,
            "RECALL_BUDGET_EXCEEDED must appear at least twice in "
            "index.ts: once for the constant definition and once for "
            "usage in the budget check logic",
        )


# --------------------------------------------------------------------
# AC E: No secrets / no raw session in receipts (schema invariant)
# --------------------------------------------------------------------


class PhaseGNoSecretsTests(unittest.TestCase):
    """Assert that phase receipt JSON never includes fields that would
    leak raw session data (prompt, reasoning, session, rawError,
    apiKey, secret, content)."""

    def setUp(self):
        self._tmpdir = tempfile.mkdtemp(prefix="phaseg_secrets_")

    def tearDown(self):
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def test_finish_receipt_schema_has_no_sensitive_field_names(self):
        """The phase receipt JSON must not contain fields named 'prompt',
        'reasoning', 'session', 'rawError', 'apiKey', 'secret', or
        'content'."""
        _run_phase(
            [
                "start", "--run-id", "run-s",
                "--phase", "p1", "--owner", "planner",
            ],
            cwd=self._tmpdir,
        )
        result = _run_phase(
            [
                "finish", "--run-id", "run-s",
                "--status", "PASS", "--summary", "OK",
            ],
            cwd=self._tmpdir,
        )
        receipt = json.loads(result.stdout)
        for key in _recursive_keys(receipt):
            self.assertNotIn(
                key, _SENSITIVE_FIELDS,
                f"receipt must not contain sensitive field '{key}'",
            )


# --------------------------------------------------------------------
# AC F: Invariant maintenance (backward compatibility)
# --------------------------------------------------------------------


class PhaseGBackwardCompatTests(unittest.TestCase):
    """Existing phase operations (start/finish/status WITHOUT the new
    args) must still work exactly as before — the new args are all
    optional and additive."""

    def setUp(self):
        self._tmpdir = tempfile.mkdtemp(prefix="phaseg_compat_")

    def tearDown(self):
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def test_start_finish_status_without_new_args(self):
        """The original start/finish/status flow must work unchanged."""
        # start
        r = _run_phase(
            [
                "start", "--run-id", "compat-1",
                "--phase", "implement", "--owner", "coder",
            ],
            cwd=self._tmpdir,
        )
        self.assertEqual(r.returncode, 0)
        start_receipt = json.loads(r.stdout)
        self.assertEqual(start_receipt["status"], "RUNNING")
        self.assertEqual(start_receipt["phase"], "implement")

        # finish
        r = _run_phase(
            [
                "finish", "--run-id", "compat-1",
                "--status", "PASS", "--summary", "Done",
            ],
            cwd=self._tmpdir,
        )
        self.assertEqual(r.returncode, 0)
        finish_receipt = json.loads(r.stdout)
        self.assertEqual(finish_receipt["status"], "PASS")
        self.assertNotIn("budget_failure", finish_receipt)
        self.assertNotIn("lineage", finish_receipt)

        # status
        r = _run_phase(
            [
                "status", "--run-id", "compat-1",
                "--phase", "implement",
            ],
            cwd=self._tmpdir,
        )
        self.assertEqual(r.returncode, 0)
        status_receipt = json.loads(r.stdout)
        self.assertEqual(status_receipt["status"], "PASS")


# --------------------------------------------------------------------
# AC G: Documentation checks
# --------------------------------------------------------------------


class PhaseGDocTests(unittest.TestCase):
    """Source-text assertions on design doc and README for Phase G."""

    def test_design_doc_marks_phase_g_implemented(self):
        """docs/teamflow-memory-context-design.md must mark Phase G as
        implemented (已实现)."""
        design = (
            DESIGN_DOC.read_text(encoding="utf-8")
            if DESIGN_DOC.is_file()
            else ""
        )
        # Must reference Phase G / 规划反馈
        self.assertTrue(
            "规划反馈" in design or "Phase G" in design
            or "阶段 G" in design,
            "design doc must contain a Phase G section",
        )
        # Phase G section must specifically contain 已实现
        g_match = re.search(
            r'###\s*G[\.\s].*?(?=\n##\s|\Z)',
            design, re.DOTALL,
        )
        self.assertIsNotNone(
            g_match,
            "design doc must contain a Phase G section header",
        )
        self.assertIn(
            "已实现",
            g_match.group(),
            "Phase G section must be marked as '已实现'",
        )

    def test_readme_documents_phase_g(self):
        """README.md must document Phase G architecture: budget failure
        receipts and planning experience."""
        readme = (
            README_FILE.read_text(encoding="utf-8")
            if README_FILE.is_file()
            else ""
        )
        has_budget = any(
            t in readme
            for t in (
                "budget failure", "budget_failure",
                "预算失败", "CONTEXT_BUDGET_EXCEEDED",
                "RECALL_BUDGET_EXCEEDED",
            )
        )
        has_planning_exp = any(
            t in readme.lower()
            for t in (
                "planning experience", "planning-experience",
                "规划经验",
            )
        )
        self.assertTrue(
            has_budget,
            "README.md must mention budget failure receipts for "
            "Phase G",
        )
        self.assertTrue(
            has_planning_exp,
            "README.md must mention planning experience for Phase G",
        )


if __name__ == "__main__":
    unittest.main()
