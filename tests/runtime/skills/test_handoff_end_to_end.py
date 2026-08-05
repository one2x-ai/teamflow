"""End-to-end contract test for one run's coordination surface.

The individual pieces are covered by their own modules; this one proves they
compose into the story the design describes, driving only the real CLIs:

    teamflow run-start -> root handoff -> child handoff -> child receipt
    -> agents list -> teamflow wait increment -> root finish -> run_finished

It is deliberately provider-free: everything asserted here is mechanical, so
it runs anywhere and pins the observable surface an outer loop depends on.

All paths are relative to the repository root
``ROOT = Path(__file__).resolve().parents[3]``.
"""

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
TEAMFLOW_BIN = ROOT / ".teamflow" / "bin" / "teamflow"

TIMEOUT = 90
RUN_ID = "run-20260101-000000-e2e0"

ROOT_BODY = """- Goal: prove the coordination surface composes.
- Scope: .teamflow/skills/write-handoff/scripts/handoff_state.py
- Acceptance: 1. the outer loop sees run_finished.
"""

CHILD_BODY = """- Goal: run the focused test command.
- Scope: tests/runtime/skills/test_handoff_end_to_end.py
- Acceptance: 1. a validated receipt lands on disk.
"""


class HandoffEndToEndTests(unittest.TestCase):
    def setUp(self):
        self._directory = tempfile.TemporaryDirectory()
        self.root = Path(self._directory.name)
        self.home = self.root / "home"
        self.home.mkdir()
        self.code = self.root / ".teamflow" / "runs" / "code"
        self.code.mkdir(parents=True)

    def tearDown(self):
        self._directory.cleanup()

    def env(self):
        env = dict(os.environ)
        for key in list(env):
            if key.startswith(("TEAMFLOW_", "WORKFLOW_", "OPENCODE_WORKFLOW_")):
                env.pop(key)
        env["HOME"] = str(self.home)
        return env

    def teamflow(self, *args, body=None, expect_ok=True):
        completed = subprocess.run(
            ["bash", str(TEAMFLOW_BIN), *args],
            cwd=str(self.root),
            input=body,
            env=self.env(),
            capture_output=True,
            text=True,
            timeout=TIMEOUT,
        )
        if expect_ok:
            self.assertEqual(completed.returncode, 0, completed.stderr)
        return completed

    def handoff(self, *args, body=None, expect_ok=True):
        return self.teamflow(
            "handoff", *args, "--runs-dir", str(self.code), "--run-id", RUN_ID,
            body=body, expect_ok=expect_ok,
        )

    def wait(self, since=0, timeout="2"):
        completed = self.teamflow(
            "wait", "--runs-dir", str(self.code), "--run-id", RUN_ID,
            "--since", str(since), "--timeout", timeout,
        )
        return json.loads(completed.stdout)

    def test_full_run_is_observable_from_metadata_alone(self):
        # The run announces itself, so the outer loop never guesses by mtime.
        self.handoff("run-start", "--role", "planner", "--pid", str(os.getpid()))

        root = json.loads(
            self.handoff("open", "--role", "planner", "--body-file", "-", body=ROOT_BODY).stdout
        )
        self.handoff("start", "--id", root["handoff_id"], "--pid", str(os.getpid()))

        child = json.loads(
            self.handoff(
                "open", "--role", "test-runner", "--parent-id", root["handoff_id"],
                "--body-file", "-", body=CHILD_BODY,
            ).stdout
        )
        self.handoff("start", "--id", child["handoff_id"], "--pid", str(os.getpid()))

        # Both handoffs are in flight at once: no single cursor to fight over.
        active = sorted(
            entry.name for entry in (self.code / RUN_ID / "active").iterdir()
        )
        self.assertEqual(active, sorted([root["handoff_id"], child["handoff_id"]]))

        board = json.loads(
            self.teamflow(
                "agents", "list", "--runs-dir", str(self.code), "--run-id", RUN_ID,
                "--format", "json",
            ).stdout
        )
        self.assertEqual(
            [(row["role"], row["depth"], row["title"]) for row in board],
            [
                ("planner", 0, "prove the coordination surface composes."),
                ("test-runner", 1, "run the focused test command."),
            ],
            "the board is derived from disk facts, sorted by depth then role",
        )
        self.assertEqual(
            board[1]["scope"], ["tests/runtime/skills/test_handoff_end_to_end.py"]
        )

        watermark = self.wait()["seq"]

        receipt = self.root / "runner-receipt.json"
        receipt.write_text(
            json.dumps(
                {
                    "status": "FAIL",
                    "command": "python3 tests/runtime/skills/test_handoff_end_to_end.py",
                    "exit_code": 1,
                    "failed_checks": ["test_full_run_is_observable_from_metadata_alone"],
                    "error_excerpt": "AssertionError: missing behavior",
                    "diagnosis": "diagnosis: the feature is not implemented yet",
                    "next_owner": "coder",
                    "expected_red": True,
                }
            ),
            encoding="utf-8",
        )
        self.handoff(
            "finish", "--id", child["handoff_id"], "--status", "FAIL",
            "--receipt", str(receipt), "--summary", "focused test is red as expected",
        )

        # The outer loop learns the child's outcome from the file name alone.
        increment = self.wait(since=watermark)
        self.assertEqual(
            [(event["kind"], event["status"]) for event in increment["events"]],
            [("handoff_finished", "FAIL")],
        )
        self.assertEqual(increment["events"][0]["subject"], child["handoff_id"])

        # Escalating to the receipt is one small read of typed fields.
        stored = json.loads(
            (
                self.code / RUN_ID / "handoffs" / child["handoff_id"] / "receipt.json"
            ).read_text(encoding="utf-8")
        )
        self.assertEqual(stored["next_owner"], "coder")
        self.assertTrue(stored["expected_red"])

        # A finished handoff leaves the active set; the board shrinks with it.
        self.assertEqual(
            [
                entry.name
                for entry in (self.code / RUN_ID / "active").iterdir()
            ],
            [root["handoff_id"]],
        )

        self.handoff(
            "finish", "--id", root["handoff_id"], "--status", "PASS",
            "--summary", "run complete",
        )

        final = self.wait(since=increment["seq"])
        self.assertEqual(
            [(event["kind"], event["status"]) for event in final["events"]],
            [("handoff_finished", "PASS"), ("run_finished", "PASS")],
            "the root handoff closing the run is what ends the outer loop's watch",
        )

        spool = sorted(
            entry.name for entry in (self.code / "_spool").iterdir() if entry.is_file()
        )
        self.assertEqual(
            [name.split("--")[2] for name in spool if name.endswith(".json")],
            ["run_started", "run_finished"],
            "cross-run discovery sees the run open and close",
        )

    def test_a_blocked_child_stops_the_run_without_a_receipt_file(self):
        self.handoff("run-start", "--role", "planner", "--pid", str(os.getpid()))
        root = json.loads(
            self.handoff("open", "--role", "planner", "--body-file", "-", body=ROOT_BODY).stdout
        )
        child = json.loads(
            self.handoff(
                "open", "--role", "test-writer", "--parent-id", root["handoff_id"],
                "--body-file", "-", body=CHILD_BODY,
            ).stdout
        )
        self.handoff(
            "finish", "--id", child["handoff_id"], "--status", "BLOCKED",
            "--blocked-reason", "OUTPUT_TRUNCATED",
            "--blocked-reason", "DELEGATION_ARTIFACT_MISSING",
            "--summary", "child was truncated before writing its patch",
        )
        events = self.wait()["events"]
        blocked = [event for event in events if event["status"] == "BLOCKED"]
        self.assertEqual(len(blocked), 1, events)

        state = json.loads(
            (
                self.code / RUN_ID / "handoffs" / child["handoff_id"] / "state.json"
            ).read_text(encoding="utf-8")
        )
        self.assertEqual(state["blocked"]["reason"], "OUTPUT_TRUNCATED")
        self.assertEqual(
            state["blocked"]["reasons"],
            ["OUTPUT_TRUNCATED", "DELEGATION_ARTIFACT_MISSING"],
        )
        self.assertFalse(
            (
                self.code / RUN_ID / "handoffs" / child["handoff_id"] / "receipt.json"
            ).exists(),
            "a blocked handoff needs no receipt file: the reason enum is the receipt",
        )

    def test_no_single_cursor_file_is_created_anywhere(self):
        self.handoff("run-start", "--role", "planner", "--pid", str(os.getpid()))
        self.handoff("open", "--role", "planner", "--body-file", "-", body=ROOT_BODY)
        offenders = [
            str(path.relative_to(self.code))
            for path in self.code.rglob("current.json")
        ]
        self.assertEqual(
            offenders, [], f"the retired single cursor must not reappear: {offenders}"
        )


if __name__ == "__main__":
    unittest.main()
