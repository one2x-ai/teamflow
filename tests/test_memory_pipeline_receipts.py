"""Requirement tests for compact, leak-free memory pipeline receipts (criterion D).

`run_pipeline` must never write raw model stdout/stderr to *.log files. On a
stage failure the manifest/stage receipt records only compact, non-sensitive
fields: agent, exit_code, output, repair_exit_code/repair_status and a
classified failure_kind. The existing JSON resume artifacts (capsule,
compressed, extracted, candidates, validation, apply) must remain.

Written BEFORE the cleanup and expected to be RED while run_pipeline still
writes emotion-detection.log / <stage>.log / formatting.log and lacks a
classified failure_kind.
"""

import importlib.util
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
PIPELINE_PATH = ROOT / ".teamflow/skills/extract-memory/scripts/run_pipeline.py"


def load_pipeline():
    spec = importlib.util.spec_from_file_location("memory_run_pipeline_receipts", PIPELINE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class PipelineSourceContractTests(unittest.TestCase):
    """D + A: run_pipeline source has no raw logs, no WORKFLOW_* fallback."""

    def setUp(self):
        self.source = PIPELINE_PATH.read_text(encoding="utf-8")

    def test_no_workflow_env_fallback(self):
        self.assertNotIn("WORKFLOW_", self.source)

    def test_no_raw_log_files(self):
        self.assertNotIn(".log", self.source)

    def test_no_raw_stdout_stderr_captured_into_files(self):
        for pattern in (
            "result.stdout + result.stderr",
            "repair_result.stdout + repair_result.stderr",
            "emotion_result.stdout + emotion_result.stderr",
        ):
            with self.subTest(pattern=pattern):
                self.assertNotIn(pattern, self.source)

    def test_existing_json_resume_artifacts_remain(self):
        for artifact in (
            "00-evidence-capsule.json",
            "10-compressed.json",
            "20-extracted.json",
            "30-candidates.json",
            "40-validation.json",
            "50-apply.json",
        ):
            with self.subTest(artifact=artifact):
                self.assertIn(artifact, self.source)


class PipelineFailureReceiptTests(unittest.TestCase):
    """D: a failing stage writes no raw logs and a compact classified receipt."""

    def setUp(self):
        self.pipeline = load_pipeline()

    def _write_project(self, project: Path) -> Path:
        (project / ".teamflow" / "bin").mkdir(parents=True)
        (project / ".teamflow" / "bin" / "teamflow").write_text("#!/bin/sh\n", encoding="utf-8")
        receipts = project / ".teamflow" / "runs" / "task-receipts" / "demo"
        receipts.mkdir(parents=True)
        receipt = {
            "schema_version": 1,
            "kind": "verified-task-receipt",
            "outcome": "PASS",
            "task": "demo task",
            "summary": "demo summary",
            "evidence": [{"status": "PASS"}],
        }
        receipt_path = receipts / "receipt.json"
        receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
        return receipt_path

    def test_failing_emotion_stage_writes_no_raw_logs(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            receipt_path = self._write_project(project)

            def fake_run_model(command, cwd):
                prompt = command[-1]
                # Emotion detection writes "strict contract JSON"; compression
                # and later stages write "strict JSON". Both run through this
                # seam because emotion and compression are launched in parallel.
                match = re.search(
                    r"(?:write|rewrite) (?:strict contract JSON|strict JSON|the full\s+strict JSON output)"
                    r"(?: to)? (\S+\.json)",
                    prompt,
                )
                assert match is not None, prompt
                (cwd / match.group(1)).write_text("{}", encoding="utf-8")
                return subprocess.CompletedProcess(
                    command, 0, "RAWMODELSTDOUT-TOPSECRET", "RAWMODELSTDERR-TOPSECRET"
                )

            old_cwd = os.getcwd()
            os.chdir(project)
            orig_argv = sys.argv
            sys.argv = ["run_pipeline.py", "--capture-file", str(receipt_path), "--run-id", "demo"]
            try:
                with mock.patch.object(self.pipeline, "run_model", side_effect=fake_run_model):
                    with self.assertRaises(SystemExit):
                        self.pipeline.main()
            finally:
                sys.argv = orig_argv
                os.chdir(old_cwd)

            run_dir = project / ".teamflow" / "runs" / "memory" / "demo"
            self.assertTrue(run_dir.is_dir(), run_dir)
            self.assertFalse(
                list(run_dir.rglob("*.log")),
                "no *.log files may be written to the run directory",
            )
            for path in run_dir.rglob("*"):
                if not path.is_file():
                    continue
                text = path.read_text(encoding="utf-8")
                with self.subTest(file=path.name):
                    self.assertNotIn("RAWMODELSTDOUT", text)
                    self.assertNotIn("RAWMODELSTDERR", text)
                    self.assertNotIn("TOPSECRET", text)

    def test_failing_emotion_stage_records_compact_classified_receipt(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            receipt_path = self._write_project(project)

            def fake_run_model(command, cwd):
                prompt = command[-1]
                match = re.search(
                    r"(?:write|rewrite) (?:strict contract JSON|strict JSON|the full\s+strict JSON output)"
                    r"(?: to)? (\S+\.json)",
                    prompt,
                )
                (cwd / match.group(1)).write_text("{}", encoding="utf-8")
                return subprocess.CompletedProcess(command, 0, "STDOUT-NOLEAK", "STDERR-NOLEAK")

            old_cwd = os.getcwd()
            os.chdir(project)
            orig_argv = sys.argv
            sys.argv = ["run_pipeline.py", "--capture-file", str(receipt_path), "--run-id", "demo"]
            try:
                with mock.patch.object(self.pipeline, "run_model", side_effect=fake_run_model):
                    with self.assertRaises(SystemExit):
                        self.pipeline.main()
            finally:
                sys.argv = orig_argv
                os.chdir(old_cwd)

            run_dir = project / ".teamflow" / "runs" / "memory" / "demo"
            manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
            emotion = manifest["stages"]["emotion_detection"]
            allowed = {
                "agent", "exit_code", "output", "repair_exit_code",
                "repair_status", "failure_kind", "parallel_group",
            }
            self.assertLessEqual(set(emotion), allowed, emotion)
            self.assertIn("failure_kind", emotion)
            self.assertTrue(emotion["failure_kind"], "failure_kind must be a non-empty classified kind")
            self.assertNotIn("stdout", emotion)
            self.assertNotIn("stderr", emotion)


if __name__ == "__main__":
    unittest.main()
