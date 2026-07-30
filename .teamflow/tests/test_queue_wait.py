import importlib.util
import json
import os
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[2]
PIPELINE_PATH = ROOT / ".teamflow/skills/extract-memory/scripts/run_pipeline.py"


def load_pipeline():
    spec = importlib.util.spec_from_file_location("workflow_run_pipeline", PIPELINE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class QueueWaitTests(unittest.TestCase):
    def test_memory_stage_timeout_is_disabled_by_default(self):
        pipeline = load_pipeline()
        self.assertIsNone(pipeline.parse_model_stage_timeout(None))
        self.assertIsNone(pipeline.parse_model_stage_timeout(""))

    def test_memory_stage_timeout_accepts_only_positive_opt_in(self):
        pipeline = load_pipeline()
        self.assertEqual(pipeline.parse_model_stage_timeout("17"), 17)
        for value in ("0", "-1", "not-a-number"):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    pipeline.parse_model_stage_timeout(value)

    def test_run_model_does_not_pass_timeout_by_default(self):
        pipeline = load_pipeline()
        process = mock.Mock(pid=1234, returncode=0)
        process.communicate.return_value = ("stdout", "stderr")
        env = os.environ.copy()
        env.pop("TEAMFLOW_MODEL_STAGE_TIMEOUT_SECONDS", None)
        with mock.patch.dict(os.environ, env, clear=True), mock.patch.object(
            pipeline.subprocess, "Popen", return_value=process
        ):
            result = pipeline.run_model(["model"], ROOT)
        process.communicate.assert_called_once_with()
        self.assertEqual(result.returncode, 0)

    def test_run_model_passes_explicit_positive_timeout(self):
        pipeline = load_pipeline()
        process = mock.Mock(pid=1234, returncode=0)
        process.communicate.return_value = ("stdout", "stderr")
        with mock.patch.dict(
            os.environ, {"TEAMFLOW_MODEL_STAGE_TIMEOUT_SECONDS": "17"}, clear=False
        ), mock.patch.object(pipeline.subprocess, "Popen", return_value=process):
            pipeline.run_model(["model"], ROOT)
        process.communicate.assert_called_once_with(timeout=17)


if __name__ == "__main__":
    unittest.main()
