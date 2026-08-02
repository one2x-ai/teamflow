"""RED tests for candidate_text content-precedence contract repair.

These tests are designed to FAIL against the current run_pipeline.py because:
  - candidate_text ignores the ``content`` field entirely.
  - validate_stage (formatting) does not reject candidates with empty semantic text.
  - apply_candidates propagates the collapsed subject-only text into write-note.

After the fix, all tests should turn GREEN.
"""

import importlib.util
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[2]
PIPELINE_PATH = ROOT / ".teamflow/skills/extract-memory/scripts/run_pipeline.py"


def load_pipeline():
    spec = importlib.util.spec_from_file_location("memory_run_pipeline", PIPELINE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


CANDIDATE_004_CONTENT = (
    "The max_concurrency configuration parameter that bounds parallel "
    "task execution in task_group. It is clamped to [1, 8] via a "
    "MAX_CONCURRENCY constant (hard ceiling 8) using "
    "Math.min(MAX_CONCURRENCY, Math.max(1, Math.floor(value ?? 3))): "
    "floor 1, ceiling 8, default 3. The hard ceiling of 8 was a deliberate "
    "decision; raising it requires updating MAX_CONCURRENCY and the "
    "parameter's documented description together. Boundary: covers the "
    "clamping range, ceiling, and default of max_concurrency; does not "
    "cover the scheduling or queueing algorithm."
)


def _formatting_value(candidates):
    return {
        "schema_version": 1,
        "stage": "formatting",
        "candidates": candidates,
        "source_disposition": [],
        "excluded": [],
        "conflicts": [],
    }


_FORMATTING_SOURCE_IDS = {"RECEIPT-1"}
_FORMATTING_NOTE_SOURCE_IDS = set()
_FORMATTING_PRIOR = {
    "compression": {"evidence": []},
    "extraction": {
        "concepts": [{"id": "concept-test"}],
        "facts": [],
        "decisions": [],
        "relations": [],
        "procedures": [],
        "problems": [],
    },
}
_FORMATTING_SOURCE_TEXT = ""


def _validate_formatting(pipeline, candidates):
    return pipeline.validate_stage(
        "formatting",
        _formatting_value(candidates),
        _FORMATTING_SOURCE_IDS,
        _FORMATTING_NOTE_SOURCE_IDS,
        _FORMATTING_PRIOR,
        _FORMATTING_SOURCE_TEXT,
    )


def _has_semantic_error(errors):
    return any("semantic" in err.lower() for err in errors)


class CandidateTextUnitTests(unittest.TestCase):
    def setUp(self):
        self.pipeline = load_pipeline()

    def test_candidate_text_prefers_content_over_statement(self):
        """content takes precedence over statement."""
        item = {"content": "Canonical content text.", "statement": "Legacy statement."}
        self.assertEqual(self.pipeline.candidate_text(item), "Canonical content text.")

    def test_candidate_text_uses_content_alone(self):
        """content by itself is sufficient and stripped."""
        item = {"content": "  Rich semantic body.  "}
        self.assertEqual(self.pipeline.candidate_text(item), "Rich semantic body.")

    def test_candidate_text_falls_back_to_statement(self):
        """statement is used when content is absent."""
        item = {"statement": "  Legacy statement line.  "}
        self.assertEqual(self.pipeline.candidate_text(item), "Legacy statement line.")

    def test_candidate_text_falls_back_to_complete_spo(self):
        """complete subject+predicate+object yields the joined triple."""
        item = {"subject": "alpha", "predicate": "relates to", "object": "beta"}
        self.assertEqual(
            self.pipeline.candidate_text(item), "alpha relates to beta"
        )

    def test_candidate_text_rejects_subject_only(self):
        """subject alone is invalid and must return empty string."""
        item = {"subject": "concept-test"}
        self.assertEqual(self.pipeline.candidate_text(item), "")

    def test_candidate_text_rejects_incomplete_spo(self):
        """subject+predicate without object is invalid."""
        item = {"subject": "alpha", "predicate": "relates to"}
        self.assertEqual(self.pipeline.candidate_text(item), "")

    def test_candidate_text_rejects_completely_empty(self):
        """empty item yields empty string."""
        self.assertEqual(self.pipeline.candidate_text({}), "")


class FormattingValidationTests(unittest.TestCase):
    def setUp(self):
        self.pipeline = load_pipeline()

    def test_validation_rejects_subject_only_create(self):
        """RED: subject-only create must produce a semantic error."""
        candidate = {
            "id": "candidate-subject-only",
            "type": "concept",
            "action": "create",
            "subject": "concept-test",
            "scope": "repository",
            "derived_from": ["concept-test"],
            "evidence_refs": ["RECEIPT-1"],
            "action_reason": "new knowledge",
        }
        errors = _validate_formatting(self.pipeline, [candidate])
        self.assertTrue(errors, "subject-only candidate must produce errors")
        self.assertTrue(
            _has_semantic_error(errors),
            "errors must mention semantic or canonical content",
        )

    def test_validation_accepts_content_create(self):
        """A create candidate with rich content must not get a semantic error."""
        candidate = {
            "id": "candidate-content",
            "type": "concept",
            "action": "create",
            "subject": "concept-test",
            "content": "Some rich semantic text about the concept.",
            "scope": "repository",
            "derived_from": ["concept-test"],
            "evidence_refs": ["RECEIPT-1"],
            "action_reason": "new knowledge",
        }
        errors = _validate_formatting(self.pipeline, [candidate])
        self.assertFalse(_has_semantic_error(errors))

    def test_validation_accepts_statement_create(self):
        """Legacy statement candidates must still pass semantic check."""
        candidate = {
            "id": "candidate-statement",
            "type": "concept",
            "action": "create",
            "subject": "concept-test",
            "statement": "Legacy statement about the concept.",
            "scope": "repository",
            "derived_from": ["concept-test"],
            "evidence_refs": ["RECEIPT-1"],
            "action_reason": "new knowledge",
        }
        errors = _validate_formatting(self.pipeline, [candidate])
        self.assertFalse(_has_semantic_error(errors))

    def test_validation_accepts_complete_spo_relation(self):
        """A relation candidate with complete SPO must pass semantic check."""
        candidate = {
            "id": "candidate-relation",
            "type": "relation",
            "action": "create",
            "subject": "alpha",
            "predicate": "relates to",
            "object": "beta",
            "scope": "repository",
            "derived_from": ["concept-test"],
            "evidence_refs": ["RECEIPT-1"],
            "action_reason": "new knowledge",
        }
        errors = _validate_formatting(self.pipeline, [candidate])
        self.assertFalse(_has_semantic_error(errors))

    def test_validation_rejects_empty_semantic_update(self):
        """RED: update candidate with no semantic text must error."""
        candidate = {
            "id": "candidate-update",
            "type": "concept",
            "action": "update",
            "subject": "concept-test",
            "scope": "repository",
            "derived_from": ["concept-test"],
            "evidence_refs": ["RECEIPT-1"],
            "action_reason": "refinement",
            "supersedes": ["note-alpha"],
        }
        errors = _validate_formatting(self.pipeline, [candidate])
        self.assertTrue(_has_semantic_error(errors))

    def test_validation_skips_exempt_from_semantic(self):
        """skip candidates are exempt from semantic text requirement."""
        candidate = {
            "id": "candidate-skip",
            "type": "concept",
            "action": "skip",
            "subject": "concept-test",
            "scope": "repository",
            "derived_from": ["concept-test"],
            "evidence_refs": ["RECEIPT-1"],
            "action_reason": "duplicate",
        }
        errors = _validate_formatting(self.pipeline, [candidate])
        self.assertFalse(_has_semantic_error(errors))


class ApplyCandidatesRegressionTests(unittest.TestCase):
    def setUp(self):
        self.pipeline = load_pipeline()

    def test_apply_writes_full_content_for_concept_candidate(self):
        """RED: candidate-004 must write the full content, not bare subject."""
        candidate = {
            "id": "candidate-004",
            "type": "concept",
            "action": "create",
            "subject": "concept-concurrency-limit",
            "content": CANDIDATE_004_CONTENT,
            "scope": "repository",
            "evidence_refs": ["RECEIPT-1"],
            "derived_from": ["concept-concurrency-limit"],
            "action_reason": "required concept",
        }
        formatting = _formatting_value([candidate])
        captured = []

        def fake_run(cmd, **kwargs):
            captured.append(cmd)
            return subprocess.CompletedProcess(cmd, 0, "", "")

        with tempfile.TemporaryDirectory() as run_dir:
            with mock.patch.object(
                self.pipeline.subprocess, "run", side_effect=fake_run
            ):
                self.pipeline.apply_candidates(
                    ROOT, Path(run_dir), formatting, "teamflow"
                )

        write_calls = [c for c in captured if "write-note" in c]
        self.assertTrue(write_calls, "write-note must be called for create candidate")
        cmd = write_calls[0]
        content_idx = cmd.index("--content")
        body = cmd[content_idx + 1]
        self.assertIn("hard ceiling 8", body)
        self.assertIn("max_concurrency", body)
        self.assertNotEqual(body.strip(), "concept-concurrency-limit")
        title_idx = cmd.index("--title")
        title = cmd[title_idx + 1]
        self.assertFalse(
            title.startswith("concept-concurrency-limit"),
            "title must start with rich content prefix, not bare subject",
        )

    def test_apply_subject_only_candidate_is_deferred(self):
        """RED: subject-only create must be deferred, not applied."""
        candidate = {
            "id": "candidate-subject-only-apply",
            "type": "concept",
            "action": "create",
            "subject": "concept-test",
            "scope": "repository",
            "evidence_refs": ["RECEIPT-1"],
            "derived_from": ["concept-test"],
            "action_reason": "new knowledge",
        }
        formatting = _formatting_value([candidate])
        captured = []

        def fake_run(cmd, **kwargs):
            captured.append(cmd)
            return subprocess.CompletedProcess(cmd, 0, "", "")

        with tempfile.TemporaryDirectory() as run_dir:
            run_dir_path = Path(run_dir)
            with mock.patch.object(
                self.pipeline.subprocess, "run", side_effect=fake_run
            ):
                self.pipeline.apply_candidates(
                    ROOT, run_dir_path, formatting, "teamflow"
                )
            apply_report = json.loads(
                (run_dir_path / "50-apply.json").read_text("utf-8")
            )

        write_calls = [c for c in captured if "write-note" in c]
        self.assertFalse(
            write_calls, "subject-only candidate must not call write-note"
        )
        self.assertEqual(
            apply_report["applied"],
            [],
            "applied list must be empty for subject-only candidate",
        )


if __name__ == "__main__":
    unittest.main()
