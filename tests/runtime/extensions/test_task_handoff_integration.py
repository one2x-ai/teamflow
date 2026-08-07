"""Requirement tests for the delegation/handoff join (design S2.5, S3 item 3).

Before this change a failure receipt lived in the child's final assistant
text: untyped, unvalidated, and free to decay into prose that both the
planner and the observe loop then paid tokens to interpret. Delegation now
materializes a handoff directory, the child writes its receipt through the
CLI (which validates it), and the tool's return value degrades to a pointer
so the receipt body never enters the delegator's context.

The extension's pure decision logic lives in ``handoff-gate.ts`` with its
own behavioral bun test; the assertions here pin the wiring that the bun
test cannot see.

All paths are relative to the repository root
``ROOT = Path(__file__).resolve().parents[3]``.
"""

import json
import os
import re
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
EXTENSION = ROOT / ".teamflow" / "extensions" / "teamflow-task" / "index.ts"
GATE = ROOT / ".teamflow" / "extensions" / "teamflow-task" / "handoff-gate.ts"
GATE_TEST = ROOT / ".teamflow" / "extensions" / "teamflow-task" / "handoff-gate.test.ts"
HANDOFF_CLI = (
    ROOT / ".teamflow" / "skills" / "write-handoff" / "scripts" / "handoff_state.py"
)
INSTALL = ROOT / "scripts" / "install.sh"

TIMEOUT = 60
RUN_ID = "run-20260101-000000-cccc"


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.is_file() else ""


def clean_env(home: Path) -> dict:
    env = dict(os.environ)
    for key in list(env):
        if key.startswith(("TEAMFLOW_", "WORKFLOW_", "OPENCODE_WORKFLOW_")):
            env.pop(key)
    env["HOME"] = str(home)
    return env


class HandoffMaterializationTests(unittest.TestCase):
    def setUp(self):
        self.text = read(EXTENSION)

    def test_extension_opens_a_handoff_through_the_cli(self):
        self.assertIn("handoff_state.py", self.text)
        self.assertRegex(
            self.text,
            r'"open"',
            "the delegation must register its handoff instead of only passing a prompt",
        )

    def test_prompt_becomes_the_handoff_body(self):
        self.assertRegex(
            self.text,
            r'"--body-file",\s*\n?\s*"-"',
            "the handoff body is the delegation prompt, delivered on stdin",
        )

    def test_child_handoff_is_opened_at_depth_one_with_parent_lineage(self):
        self.assertIn("--parent-id", self.text)
        self.assertRegex(self.text, r'"--depth",\s*\n?\s*"1"')

    def test_handoff_id_is_injected_into_the_child_environment(self):
        self.assertRegex(
            self.text,
            r"childEnv[.\[]\s*\"?TEAMFLOW_HANDOFF_ID|TEAMFLOW_HANDOFF_ID\s*:",
            "the child needs its handoff id to write its own receipt",
        )

    def test_run_id_is_propagated_to_the_child(self):
        self.assertIn("TEAMFLOW_RUN_ID", self.text)


class ReceiptReconciliationTests(unittest.TestCase):
    def setUp(self):
        self.text = read(EXTENSION)

    def test_extension_uses_the_pure_gate_module(self):
        self.assertTrue(GATE.is_file(), "handoff-gate.ts must exist")
        self.assertTrue(
            GATE_TEST.is_file(), "the gate's decision logic needs a behavioral bun test"
        )
        self.assertIn("./handoff-gate", self.text)
        self.assertIn("blockedReasons", self.text)

    def test_extension_checks_that_the_receipt_landed(self):
        self.assertRegex(
            self.text,
            r"existsSync\s*\(\s*handoff\.receiptPath\s*\)",
            "presence of the receipt artifact is the parent's mechanical check",
        )
        self.assertIn("receiptPresent", self.text)

    def test_extension_writes_blocked_instead_of_retrying(self):
        self.assertRegex(self.text, r'"finish"')
        self.assertIn("--blocked-reason", self.text)
        self.assertIn("BLOCKED", self.text)
        self.assertNotRegex(
            self.text,
            r"(?i)for\s*\(\s*let\s+attempt|retry\s*\+\+|maxRetries",
            "a truncated or artifact-less delegation must not be retried silently",
        )

    def test_extension_does_not_overwrite_a_terminal_handoff(self):
        self.assertRegex(
            self.text,
            r"(?i)terminal|already (done|blocked|finished)",
            "the child owns its own terminal receipt; the parent must not rewrite it",
        )


class PointerReturnTests(unittest.TestCase):
    def setUp(self):
        self.text = read(EXTENSION)

    def test_task_returns_a_pointer(self):
        self.assertIn("delegationPointer", self.text)

    def test_task_group_returns_pointers_too(self):
        index = self.text.find("task_group")
        self.assertGreater(index, -1)
        self.assertIn(
            "delegationPointer",
            self.text[index:],
            "task_group must pointer-ize each result the same way",
        )


class ChildWatchdogCoverageTests(unittest.TestCase):
    def setUp(self):
        self.text = read(EXTENSION)

    def test_child_argv_loads_the_watchdog_extension(self):
        self.assertIn("agent-watchdog", self.text)
        self.assertIn("--extension", self.text)


class ReceiptIsMandatoryForChildrenTests(unittest.TestCase):
    """A child cannot claim PASS or FAIL without a validated receipt."""

    def _fixture(self, directory: Path):
        home = directory / "home"
        home.mkdir()
        code = directory / ".teamflow" / "runs" / "code"
        code.mkdir(parents=True)
        return home, code

    def _cli(self, directory, home, code, *args, body=None):
        return subprocess.run(
            ["python3", str(HANDOFF_CLI), *args, "--runs-dir", str(code),
             "--run-id", RUN_ID],
            cwd=str(directory), input=body, env=clean_env(home),
            capture_output=True, text=True, timeout=TIMEOUT,
        )

    def _open_child(self, directory, home, code, role="test-runner"):
        parent = json.loads(
            self._cli(
                directory, home, code, "handoff", "open", "--role", "planner",
                "--body-file", "-", body="- Goal: root.\n",
            ).stdout
        )
        return json.loads(
            self._cli(
                directory, home, code, "handoff", "open", "--role", role,
                "--parent-id", parent["handoff_id"], "--body-file", "-",
                body="- Goal: child work.\n",
            ).stdout
        )

    def test_child_pass_without_a_receipt_is_rejected(self):
        with tempfile.TemporaryDirectory() as name:
            directory = Path(name)
            home, code = self._fixture(directory)
            child = self._open_child(directory, home, code)
            completed = self._cli(
                directory, home, code, "handoff", "finish",
                "--id", child["handoff_id"], "--status", "PASS", "--summary", "s",
            )
            self.assertNotEqual(
                completed.returncode, 0,
                "a delegated PASS with no receipt is the untyped-prose failure mode",
            )
            self.assertIn("receipt", completed.stderr)

    def test_child_blocked_needs_no_receipt(self):
        """The reason enum *is* the receipt for a blocked handoff."""
        with tempfile.TemporaryDirectory() as name:
            directory = Path(name)
            home, code = self._fixture(directory)
            child = self._open_child(directory, home, code)
            completed = self._cli(
                directory, home, code, "handoff", "finish",
                "--id", child["handoff_id"], "--status", "BLOCKED",
                "--blocked-reason", "PROVIDER_FAILURE", "--summary", "s",
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)

    def test_root_handoff_may_finish_without_a_receipt_file(self):
        """The planner's summary is not a delegated receipt."""
        with tempfile.TemporaryDirectory() as name:
            directory = Path(name)
            home, code = self._fixture(directory)
            root = json.loads(
                self._cli(
                    directory, home, code, "handoff", "open", "--role", "planner",
                    "--body-file", "-", body="- Goal: root.\n",
                ).stdout
            )
            completed = self._cli(
                directory, home, code, "handoff", "finish",
                "--id", root["handoff_id"], "--status", "PASS", "--summary", "s",
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)


class InstallerShipsGateTests(unittest.TestCase):
    def test_installer_ships_the_gate_module(self):
        self.assertIn(".teamflow/extensions/teamflow-task/handoff-gate.ts", read(INSTALL))

    def test_installer_does_not_ship_the_gate_test(self):
        self.assertNotIn("handoff-gate.test.ts", read(INSTALL))


if __name__ == "__main__":
    unittest.main()
