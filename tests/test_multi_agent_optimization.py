"""Requirement tests for Phase 1 multi-agent optimizations.

Covers three contracts from docs/multi-agent-optimization-design.md:

1. Supervisor role: a lightweight mechanical-check role (MiMo) that
   verifies artifacts, checksums, and test-patch gates without editing
   files or delegating.
2. Test-runner batch mode: test-runner accepts a JSON batch of commands
   in one delegation and returns one receipt per command.
3. Evidence compression: planner handoffs carry only compressed evidence
   (failed_checks, error_excerpt, diagnosis), not raw command output.
"""

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
AGENTS_DIR = ROOT / ".teamflow" / "agents"


def _parse_frontmatter(text):
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}
    fm = {}
    for line in lines[1:]:
        if line.strip() == "---":
            break
        if ":" in line and not line.startswith((" ", "\t")):
            key, _, value = line.partition(":")
            fm[key.strip()] = value.strip()
    return fm


class SupervisorRoleTests(unittest.TestCase):
    """Contract 1: supervisor role exists with correct frontmatter."""

    def setUp(self):
        self.path = AGENTS_DIR / "supervisor.md"

    def test_supervisor_file_exists(self):
        self.assertTrue(
            self.path.is_file(),
            ".teamflow/agents/supervisor.md must exist",
        )

    def test_supervisor_uses_mimo(self):
        fm = _parse_frontmatter(self.path.read_text(encoding="utf-8"))
        self.assertEqual(fm.get("model"), "mimo/mimo-v2.5-pro")

    def test_supervisor_tools_exclude_edit(self):
        fm = _parse_frontmatter(self.path.read_text(encoding="utf-8"))
        tools = fm.get("tools", "")
        self.assertNotIn("edit", tools)
        self.assertIn("read", tools)
        self.assertIn("bash", tools)

    def test_supervisor_skips_project_rules(self):
        fm = _parse_frontmatter(self.path.read_text(encoding="utf-8"))
        self.assertEqual(fm.get("needs_project_rules"), "false")

    def test_supervisor_does_not_delegate(self):
        fm = _parse_frontmatter(self.path.read_text(encoding="utf-8"))
        self.assertNotEqual(fm.get("delegates"), "true")

    def test_supervisor_forbids_file_edits(self):
        body = self.path.read_text(encoding="utf-8")
        self.assertRegex(body, r"[Nn]ever (edit|modify)")

    def test_supervisor_covers_mechanical_checks(self):
        body = self.path.read_text(encoding="utf-8").lower()
        self.assertIn("test-patch", body)


class TestRunnerBatchModeTests(unittest.TestCase):
    """Contract 2: test-runner documents batch command format."""

    def setUp(self):
        self.text = (AGENTS_DIR / "test-runner.md").read_text(encoding="utf-8")

    def test_documents_batch_format(self):
        """test-runner.md must document accepting a batch of commands."""
        self.assertRegex(
            self.text,
            r"[Bb]atch",
            "test-runner.md must document batch command mode",
        )

    def test_batch_returns_receipt_per_command(self):
        """Batch mode must return one receipt per command."""
        lowered = self.text.lower()
        self.assertIn("batch", lowered)
        self.assertIn("receipt", lowered)

    def test_single_command_still_supported(self):
        """Backward compatibility: single-command handoff remains valid."""
        self.assertIn("command", self.text)
        self.assertIn("receipt", self.text)


class EvidenceCompressionTests(unittest.TestCase):
    """Contract 3: planner handoffs use compressed evidence only."""

    def setUp(self):
        self.planner = (AGENTS_DIR / "planner.md").read_text(encoding="utf-8")

    def test_planner_documents_evidence_compression(self):
        """planner.md must document evidence compression in handoffs."""
        self.assertRegex(
            self.planner,
            r"[Ee]vidence",
        )
        self.assertRegex(
            self.planner,
            r"error_excerpt|failed_checks|diagnosis",
            "planner.md must reference compressed evidence fields",
        )

    def test_planner_stores_raw_output_to_runs(self):
        """Raw command output goes to .teamflow/runs/, not handoffs."""
        self.assertIn(".teamflow/runs/", self.planner)


class ParallelTestGenerationTests(unittest.TestCase):
    """Contract 4: bounded parallel test generation for independent modules."""

    def setUp(self):
        self.planner = (AGENTS_DIR / "planner.md").read_text(encoding="utf-8")
        self.skill = (
            ROOT / ".teamflow" / "skills" / "write-tests" / "SKILL.md"
        ).read_text(encoding="utf-8")

    def test_planner_documents_parallel_test_generation(self):
        """planner.md must document task_group parallelism for independent pairs."""
        self.assertRegex(
            self.planner,
            r"task_group",
            "planner.md must reference task_group for parallel test generation",
        )
        self.assertRegex(
            self.planner,
            r"independent",
            "planner.md must scope parallelism to independent module pairs",
        )

    def test_planner_bounds_parallelism(self):
        """Parallel test generation must be bounded (max concurrency)."""
        self.assertRegex(
            self.planner,
            r"[Mm]ax(imum)?\s+\d|at most \d|up to \d",
            "planner.md must bound parallel test-writer concurrency",
        )

    def test_planner_serial_fallback_on_conflict(self):
        """Any scope conflict falls back to serial processing."""
        self.assertRegex(
            self.planner,
            r"serial|fallback|fall back",
            "planner.md must document serial fallback on conflict",
        )

    def test_skill_documents_scope_isolation(self):
        """write-tests SKILL must document disjoint file scope for parallel writers."""
        self.assertRegex(
            self.skill,
            r"scope",
            "write-tests SKILL.md must document assigned file scope",
        )
        self.assertIn(
            "Never touch other scopes' files",
            self.skill,
            "write-tests SKILL.md must forbid writing outside assigned scope",
        )


class MemoryCaptureParallelTests(unittest.TestCase):
    """Contract 5: emotion and compression run concurrently."""

    def setUp(self):
        self.pipeline = (
            ROOT / ".teamflow" / "skills" / "extract-memory" / "scripts" / "run_pipeline.py"
        ).read_text(encoding="utf-8")

    def test_pipeline_spawns_emotion_and_compression_concurrently(self):
        """run_pipeline.py must start emotion and compression in parallel."""
        self.assertIn(
            "run_stage_group",
            self.pipeline,
            "run_pipeline.py must define a grouped parallel stage runner",
        )
        self.assertRegex(
            self.pipeline,
            r"emotion_detection.*compression|compression.*emotion_detection",
            "parallel group must include emotion_detection and compression",
        )

    def test_compression_is_gated_on_emotion_artifact(self):
        """Compression must wait for the emotion artifact, not race it."""
        self.assertIn(
            "gates",
            self.pipeline,
            "run_stage_group must support artifact gating",
        )
        self.assertRegex(
            self.pipeline,
            r'gates\s*=\s*\{\s*"compression":\s*emotion_output',
            "compression must be gated on the emotion artifact",
        )

    def test_gate_wait_is_bounded(self):
        """A missing gate artifact must never hang the run forever."""
        self.assertIn("GATE_WAIT_CEILING_SECONDS", self.pipeline)
        self.assertRegex(
            self.pipeline,
            r"deadline|time\.monotonic",
            "gate wait must be bounded by a deadline",
        )

    def test_compression_handles_missing_emotion_artifact(self):
        """Compression prompt must define the absent-emotion exclusion reason."""
        self.assertIn(
            "build_compression_extra_inputs",
            self.pipeline,
            "run_pipeline.py must build the compression prompt in one place",
        )
        start = self.pipeline.index("def build_compression_extra_inputs")
        end = self.pipeline.index("def validate_capture_receipt", start)
        compression_prompt = self.pipeline[start:end]
        self.assertIn(
            "emotion signals unavailable",
            compression_prompt,
            "compression prompt must define the absent-emotion exclusion reason",
        )
        self.assertRegex(
            compression_prompt,
            r"when present|if that artifact is missing",
            "compression prompt must describe both present and missing cases",
        )

    def test_emotion_validation_semantics_unchanged(self):
        """Emotion validation failure still fails the whole run."""
        self.assertIn("emotion detection validation failed", self.pipeline)
        self.assertIn("validate_emotion", self.pipeline)


if __name__ == "__main__":
    unittest.main()
