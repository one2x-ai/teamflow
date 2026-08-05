"""Requirement tests for the ``agent-watchdog`` extension (design S2.8).

Coverage must come from the extension loading mechanism, never from a
prompt: an agent that has to remember to announce itself will eventually
forget, and then liveness is silently wrong. The extension therefore does
two mechanical things on its first agent start — mark its handoff running,
and ignite a detached watchdog for its own pid — and registers no tools at
all.

The watchdog is spawned detached with ignored stdio and unreferenced so it
survives the process it monitors; that is the whole point, because a plugin
inside a SIGKILLed process cannot report its own death.

All paths are relative to the repository root
``ROOT = Path(__file__).resolve().parents[3]``.
"""

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
EXTENSION = ROOT / ".teamflow" / "extensions" / "agent-watchdog" / "index.ts"
WATCHDOG = (
    ROOT / ".teamflow" / "skills" / "observe-inner-loop" / "scripts" / "watchdog.py"
)
INSTALL = ROOT / "scripts" / "install.sh"
DOCTOR = ROOT / "scripts" / "doctor.sh"
TASK_EXTENSION = ROOT / ".teamflow" / "extensions" / "teamflow-task" / "index.ts"


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.is_file() else ""


class ExtensionFileTests(unittest.TestCase):
    def setUp(self):
        self.text = read(EXTENSION)

    def test_extension_file_exists(self):
        self.assertTrue(
            EXTENSION.is_file(),
            ".teamflow/extensions/agent-watchdog/index.ts must exist",
        )

    def test_registers_an_agent_start_hook(self):
        self.assertTrue(
            "before_agent_start" in self.text or "session_start" in self.text,
            "ignition must hang on a lifecycle hook, not on a prompt",
        )

    def test_ignition_happens_at_most_once_per_process(self):
        self.assertRegex(
            self.text,
            r"(?i)(ignited|started|armed|once)\s*=|\bif\s*\(\s*(ignited|armed|started)\s*\)",
            "a one-shot flag must keep one process from spawning many watchdogs",
        )

    def test_registers_no_tools(self):
        self.assertNotIn(
            "registerTool",
            self.text,
            "the watchdog extension is mechanical; it exposes no model surface",
        )


class DetachedSpawnTests(unittest.TestCase):
    def setUp(self):
        self.text = read(EXTENSION)

    def test_spawns_the_watchdog_detached(self):
        self.assertRegex(
            self.text,
            r"detached\s*:\s*true",
            "the monitor must outlive the process it monitors",
        )

    def test_spawn_ignores_stdio_and_unrefs(self):
        self.assertRegex(self.text, r'stdio\s*:\s*"ignore"')
        self.assertIn(
            "unref()",
            self.text,
            "an un-unref'd child keeps the parent's event loop alive",
        )

    def test_spawn_targets_the_watchdog_script(self):
        self.assertIn("watchdog.py", self.text)
        self.assertTrue(
            WATCHDOG.is_file(), "the spawn target must actually exist in the runtime"
        )

    def test_passes_pid_role_depth_and_run_id(self):
        for flag in ("--pid", "--role", "--depth", "--run-id"):
            with self.subTest(flag=flag):
                self.assertIn(flag, self.text)
        self.assertIn(
            "process.pid",
            self.text,
            "the watchdog must monitor this process, not a guessed one",
        )

    def test_reads_role_depth_and_run_id_from_the_environment(self):
        for variable in (
            "TEAMFLOW_AGENT_ROLE",
            "TEAMFLOW_AGENT_DEPTH",
            "TEAMFLOW_RUN_ID",
        ):
            with self.subTest(variable=variable):
                self.assertIn(variable, self.text)


class HandoffStartTests(unittest.TestCase):
    """The open -> running transition must not depend on the model."""

    def setUp(self):
        self.text = read(EXTENSION)

    def test_marks_its_handoff_running(self):
        self.assertIn("TEAMFLOW_HANDOFF_ID", self.text)
        self.assertRegex(
            self.text,
            r'"start"',
            "the receiver marks its own handoff running through the CLI",
        )
        self.assertIn("handoff_state.py", self.text)

    def test_degrades_without_a_run_id(self):
        """Running pi by hand must not break; there is simply no run to record."""
        self.assertRegex(
            self.text,
            r"if\s*\(\s*!\s*runId|runId\s*(===|==)\s*undefined|!runId",
            "a missing run-id must short-circuit instead of throwing",
        )


class IsolationTests(unittest.TestCase):
    def setUp(self):
        self.text = read(EXTENSION)

    def test_imports_resolve_without_a_local_npm_install(self):
        offenders = []
        for match in re.finditer(r'from\s+"([^"]+)"', self.text):
            specifier = match.group(1)
            if (
                specifier.startswith("@earendil-works/")
                or specifier == "typebox"
                or specifier.startswith("node:")
                or specifier.startswith(".")
            ):
                continue
            offenders.append(specifier)
        self.assertEqual(offenders, [], f"unresolvable imports: {offenders}")

    def test_extension_does_not_touch_session_or_credential_data(self):
        for forbidden in ("apiKey", "auth.json", "models.json", "sessions/"):
            with self.subTest(token=forbidden):
                self.assertNotIn(forbidden, self.text)


class ChildProcessCoverageTests(unittest.TestCase):
    """Depth-1 children must load the watchdog too, or liveness has holes."""

    def test_task_extension_loads_the_watchdog_into_children(self):
        self.assertIn(
            "agent-watchdog",
            read(TASK_EXTENSION),
            "a delegated child is a pi process and needs the same coverage",
        )


class WiringTests(unittest.TestCase):
    def test_installer_ships_the_extension(self):
        self.assertIn(".teamflow/extensions/agent-watchdog/index.ts", read(INSTALL))

    def test_doctor_verifies_the_extension(self):
        self.assertIn("agent-watchdog", read(DOCTOR))


if __name__ == "__main__":
    unittest.main()
