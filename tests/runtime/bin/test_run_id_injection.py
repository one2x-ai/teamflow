"""Requirement tests for run-id allocation and injection (design S2.7).

The outer loop used to guess the current run by directory mtime, and
``probe`` had to scan the process table for any ``pi``. Both are guesses.
``teamflow run`` now allocates the run-id itself, exports it as
``TEAMFLOW_RUN_ID`` so every child inherits it, records the depth-0 pid in
``runner.json``, and prints one machine-readable line the outer loop can
read without parsing prose.

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
WRAPPER = ROOT / ".teamflow" / "bin" / "teamflow"
PI_RUNTIME = ROOT / ".teamflow" / "bin" / "pi-runtime"

TIMEOUT = 60

#: One line, key=value, byte-stable so it never disturbs a KV cache.
RUN_LINE = re.compile(
    r"^teamflow run_id=(?P<run_id>[a-z0-9-]+) run_dir=(?P<run_dir>\S+) "
    r"handoff_id=(?P<handoff_id>\S+)$",
    re.MULTILINE,
)


def clean_env(home: Path) -> dict:
    env = dict(os.environ)
    for key in list(env):
        if key.startswith(("TEAMFLOW_", "WORKFLOW_", "OPENCODE_WORKFLOW_")):
            env.pop(key)
    env["HOME"] = str(home)
    return env


def run_print(project: Path, home: Path, *extra, env_overrides=None):
    env = clean_env(home)
    if env_overrides:
        env.update(env_overrides)
    completed = subprocess.run(
        ["python3", str(PI_RUNTIME), "run", "--agent", "planner", "ping", "--print", *extra],
        cwd=str(project),
        env=env,
        capture_output=True,
        text=True,
        timeout=TIMEOUT,
        stdin=subprocess.DEVNULL,
    )
    return completed


class RunIdAllocationTests(unittest.TestCase):
    def _project(self, directory: Path):
        project = directory / "project"
        (project / ".teamflow" / "runs" / "code").mkdir(parents=True)
        (directory / "home").mkdir()
        return project, directory / "home"

    def test_print_payload_exports_run_id(self):
        with tempfile.TemporaryDirectory() as directory:
            project, home = self._project(Path(directory))
            completed = run_print(project, home)
            self.assertEqual(completed.returncode, 0, completed.stderr)
            payload = json.loads(completed.stdout)
            run_id = payload["env"].get("TEAMFLOW_RUN_ID")
            self.assertTrue(run_id, "TEAMFLOW_RUN_ID must be exported to the child")
            self.assertRegex(
                run_id,
                r"^run-\d{8}-\d{6}-[0-9a-f]{4}$",
                "run-id must be a sortable, filesystem-safe identifier",
            )

    def test_run_id_only_uses_event_filename_safe_characters(self):
        with tempfile.TemporaryDirectory() as directory:
            project, home = self._project(Path(directory))
            payload = json.loads(run_print(project, home).stdout)
            self.assertRegex(payload["env"]["TEAMFLOW_RUN_ID"], r"^[a-z0-9-]+$")

    def test_two_runs_get_distinct_ids(self):
        with tempfile.TemporaryDirectory() as directory:
            project, home = self._project(Path(directory))
            first = json.loads(run_print(project, home).stdout)
            second = json.loads(run_print(project, home).stdout)
            self.assertNotEqual(
                first["env"]["TEAMFLOW_RUN_ID"], second["env"]["TEAMFLOW_RUN_ID"]
            )

    def test_existing_run_id_is_inherited_not_replaced(self):
        """A nested invocation must stay inside the same run."""
        with tempfile.TemporaryDirectory() as directory:
            project, home = self._project(Path(directory))
            completed = run_print(
                project, home, env_overrides={"TEAMFLOW_RUN_ID": "run-20260101-000000-abcd"}
            )
            payload = json.loads(completed.stdout)
            self.assertEqual(
                payload["env"]["TEAMFLOW_RUN_ID"], "run-20260101-000000-abcd"
            )

    def test_fresh_run_assigns_depth_zero(self):
        """A fresh top-level run (no inherited TEAMFLOW_RUN_ID) runs at depth 0."""
        with tempfile.TemporaryDirectory() as directory:
            project, home = self._project(Path(directory))
            completed = run_print(project, home)
            self.assertEqual(completed.returncode, 0, completed.stderr)
            payload = json.loads(completed.stdout)
            self.assertEqual(
                payload["env"]["TEAMFLOW_AGENT_DEPTH"], "0",
                "a fresh top-level runner must start at depth 0",
            )

    def test_nested_run_assigns_depth_one(self):
        """A run started inside an existing run (inherited TEAMFLOW_RUN_ID) is an
        auxiliary helper and must run at depth 1 so its exit never emits a
        run-level runner_exited stop signal."""
        with tempfile.TemporaryDirectory() as directory:
            project, home = self._project(Path(directory))
            completed = run_print(
                project, home, env_overrides={"TEAMFLOW_RUN_ID": "run-20260101-000000-abcd"}
            )
            payload = json.loads(completed.stdout)
            self.assertEqual(
                payload["env"]["TEAMFLOW_AGENT_DEPTH"], "1",
                "an auxiliary agent spawned inside a run must be depth 1",
            )

    def test_machine_readable_line_is_printed_on_stderr(self):
        """stdout carries the pi JSON stream, so the receipt line goes to stderr."""
        with tempfile.TemporaryDirectory() as directory:
            project, home = self._project(Path(directory))
            completed = run_print(project, home)
            match = RUN_LINE.search(completed.stderr)
            self.assertIsNotNone(
                match,
                f"stderr must carry one machine-readable run line: {completed.stderr!r}",
            )
            payload = json.loads(completed.stdout)
            self.assertEqual(
                match.group("run_id"), payload["env"]["TEAMFLOW_RUN_ID"]
            )
            for line in completed.stdout.splitlines():
                self.assertNotIn(
                    "run_id=", line, "the JSON stream on stdout must stay parseable"
                )

    def test_run_start_records_runner_json_and_spool_event(self):
        with tempfile.TemporaryDirectory() as directory:
            project, home = self._project(Path(directory))
            completed = run_print(project, home)
            run_id = json.loads(completed.stdout)["env"]["TEAMFLOW_RUN_ID"]
            code = project / ".teamflow" / "runs" / "code"
            runner = code / run_id / "runner.json"
            self.assertTrue(runner.is_file(), "runner.json must record the depth-0 pid")
            record = json.loads(runner.read_text(encoding="utf-8"))
            self.assertEqual(record["role"], "planner")
            self.assertIsInstance(record["pid"], int)
            spool = sorted(p.name for p in (code / "_spool").glob("*.json"))
            self.assertTrue(
                any("run_started" in name for name in spool),
                f"the spool must announce the new run for cross-run discovery: {spool}",
            )

    def test_root_handoff_is_opened_from_a_handoff_file(self):
        with tempfile.TemporaryDirectory() as directory:
            project, home = self._project(Path(directory))
            body = project / "root-handoff.md"
            body.write_text(
                "- Goal: the outer loop's delegation has a persisted body.\n"
                "- Scope: .teamflow/bin/pi-runtime\n",
                encoding="utf-8",
            )
            completed = run_print(project, home, "--handoff-file", str(body))
            self.assertEqual(completed.returncode, 0, completed.stderr)
            payload = json.loads(completed.stdout)
            handoff_id = payload["env"].get("TEAMFLOW_HANDOFF_ID")
            self.assertTrue(handoff_id, "the root handoff id must reach the child")
            run_id = payload["env"]["TEAMFLOW_RUN_ID"]
            base = (
                project / ".teamflow" / "runs" / "code" / run_id / "handoffs" / handoff_id
            )
            self.assertEqual(
                (base / "handoff.md").read_text(encoding="utf-8"),
                body.read_text(encoding="utf-8"),
                "the outer loop's handoff must be persisted verbatim",
            )
            state = json.loads((base / "state.json").read_text(encoding="utf-8"))
            self.assertEqual(state["depth"], 0, "the root handoff runs at depth 0")
            self.assertIsNone(state["lineage"]["parent_handoff_id"])
            match = RUN_LINE.search(completed.stderr)
            self.assertEqual(match.group("handoff_id"), handoff_id)

    def test_missing_handoff_file_fails_loudly(self):
        with tempfile.TemporaryDirectory() as directory:
            project, home = self._project(Path(directory))
            completed = run_print(project, home, "--handoff-file", "does-not-exist.md")
            self.assertNotEqual(
                completed.returncode, 0,
                "a delegation whose body cannot be read must not start",
            )

    def test_handoff_id_is_dash_when_no_root_handoff(self):
        with tempfile.TemporaryDirectory() as directory:
            project, home = self._project(Path(directory))
            match = RUN_LINE.search(run_print(project, home).stderr)
            self.assertEqual(match.group("handoff_id"), "-")


class WatchdogExtensionWiringTests(unittest.TestCase):
    """S2.8: liveness coverage comes from extension loading, not a prompt."""

    def _argv(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            project = base / "project"
            (project / ".teamflow" / "runs" / "code").mkdir(parents=True)
            (base / "home").mkdir()
            completed = run_print(project, base / "home")
            self.assertEqual(completed.returncode, 0, completed.stderr)
            return json.loads(completed.stdout)["argv"]

    def test_depth_zero_argv_loads_the_watchdog_extension(self):
        values = self._argv()
        self.assertTrue(
            any("agent-watchdog" in value for value in values),
            f"pi-runtime must load the agent-watchdog extension: {values}",
        )

    def test_existing_extensions_are_still_loaded(self):
        values = self._argv()
        for expected in ("teamflow-task", "memory-context"):
            with self.subTest(extension=expected):
                self.assertTrue(any(expected in value for value in values))


class TeamflowWrapperDispatchTests(unittest.TestCase):
    def test_wrapper_dispatches_wait(self):
        text = WRAPPER.read_text(encoding="utf-8")
        self.assertIn("wait", text)
        self.assertIn("wait.py", text)


if __name__ == "__main__":
    unittest.main()
