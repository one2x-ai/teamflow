import json
import os
import subprocess
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PHASE_STATE = ROOT / ".teamflow/skills/plan-change/scripts/phase_state.py"


class RemainingNamespaceEdgeTests(unittest.TestCase):
    """A: only the teamflow namespace remains; no legacy WORKFLOW_* fallback."""

    def test_memory_compare_uses_teamflow_command(self):
        compare = (
            ROOT / ".teamflow/experiments/scripts/compare_stage.py"
        ).read_text(encoding="utf-8")
        self.assertIn('runtime / "bin" / "teamflow"', compare)
        self.assertNotIn('runtime / "bin" / "workflow"', compare)

    def test_phase_state_has_no_workflow_env_fallback(self):
        source = PHASE_STATE.read_text(encoding="utf-8")
        self.assertNotIn("WORKFLOW_PHASE_TIMEOUT_SECONDS", source)
        self.assertNotIn("OPENCODE_WORKFLOW", source)

    def test_phase_timeout_uses_teamflow_env_only(self):
        started = (datetime.now(timezone.utc) - timedelta(seconds=10)).isoformat()
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            phase = project / ".teamflow/runs/code/example/phases/one.json"
            phase.parent.mkdir(parents=True)
            phase.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "run_id": "example",
                        "phase": "one",
                        "status": "RUNNING",
                        "started_at": started,
                    }
                ),
                encoding="utf-8",
            )
            current = project / ".teamflow/runs/code/example/current.json"
            current.write_text(
                json.dumps({"phase": "one", "path": str(phase)}), encoding="utf-8"
            )
            base_env = os.environ.copy()
            base_env.pop("TEAMFLOW_PHASE_TIMEOUT_SECONDS", None)
            base_env.pop("WORKFLOW_PHASE_TIMEOUT_SECONDS", None)

            # TEAMFLOW_PHASE_TIMEOUT_SECONDS is honored (10s elapsed > 1s).
            primary = subprocess.run(
                ["python3", str(PHASE_STATE), "status", "--run-id", "example"],
                cwd=project,
                env=base_env | {"TEAMFLOW_PHASE_TIMEOUT_SECONDS": "1"},
                text=True,
                capture_output=True,
                check=True,
            )
            self.assertIs(json.loads(primary.stdout)["stale"], True)

            # WORKFLOW_PHASE_TIMEOUT_SECONDS is ignored (default 600s, 10s elapsed).
            ignored = subprocess.run(
                ["python3", str(PHASE_STATE), "status", "--run-id", "example"],
                cwd=project,
                env=base_env | {"WORKFLOW_PHASE_TIMEOUT_SECONDS": "1"},
                text=True,
                capture_output=True,
                check=True,
            )
            self.assertIs(json.loads(ignored.stdout)["stale"], False)
