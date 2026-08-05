"""Requirement tests for the liveness watchdog (design S2.8).

Liveness and business events are orthogonal channels, and the outer loop
spends zero tokens on liveness. Two facts drive the design:

1. A plugin lives *inside* the pi process. When that process is SIGKILLed or
   OOM-killed the plugin dies with it, so the process cannot file its own
   "I died" receipt. The monitor must be a separate process that outlives it.
2. A depth-1 child's exit is already observed by the parent delegation, so
   publishing it to the event stream would be noise. Only a depth-0 exit
   means nobody is left to report that the inner loop stopped.

All paths are relative to the repository root
``ROOT = Path(__file__).resolve().parents[3]``.
"""

import json
import os
import re
import signal
import subprocess
import tempfile
import time
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
WATCHDOG = (
    ROOT / ".teamflow" / "skills" / "observe-inner-loop" / "scripts" / "watchdog.py"
)

RUN_ID = "run-20260101-000000-bbbb"
DEADLINE = 25.0

EVENT_NAME = re.compile(
    r"^(?P<seq>\d{5})--(?P<subject>[a-z0-9-]+)--(?P<kind>[a-z_]+)--(?P<status>[A-Z_]+)\.json$"
)


def clean_env(home: Path) -> dict:
    env = dict(os.environ)
    for key in list(env):
        if key.startswith(("TEAMFLOW_", "WORKFLOW_", "OPENCODE_WORKFLOW_")):
            env.pop(key)
    env["HOME"] = str(home)
    return env


def wait_until(predicate, deadline=DEADLINE):
    limit = time.monotonic() + deadline
    while time.monotonic() < limit:
        value = predicate()
        if value:
            return value
        time.sleep(0.05)
    return None


class WatchdogFixture:
    def __init__(self, directory: Path):
        self.root = directory
        self.home = directory / "home"
        self.home.mkdir(parents=True, exist_ok=True)
        self.code = directory / ".teamflow" / "runs" / "code"
        self.code.mkdir(parents=True, exist_ok=True)
        self.monitored = None
        self.watchdog = None

    @property
    def liveness(self):
        return self.code / RUN_ID / "liveness"

    @property
    def events(self):
        return self.code / RUN_ID / "events"

    def start_monitored(self, seconds=120):
        self.monitored = subprocess.Popen(
            ["sleep", str(seconds)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return self.monitored.pid

    def start_watchdog(self, pid, role="planner", depth=0, interval="0.2"):
        self.watchdog = subprocess.Popen(
            [
                "python3", str(WATCHDOG),
                "--pid", str(pid),
                "--role", role,
                "--depth", str(depth),
                "--run-id", RUN_ID,
                "--runs-dir", str(self.code),
                "--interval", interval,
            ],
            cwd=str(self.root),
            env=clean_env(self.home),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
        return self.watchdog

    def record(self, pid, role="planner", depth=0):
        path = self.liveness / f"{pid}--{role}--{depth}.json"
        if not path.is_file():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except ValueError:
            return None

    def event_kinds(self):
        if not self.events.is_dir():
            return []
        return [
            EVENT_NAME.match(path.name).group("kind")
            for path in sorted(self.events.iterdir())
            if EVENT_NAME.match(path.name)
        ]

    def cleanup(self):
        for process in (self.watchdog, self.monitored):
            if process is None or process.poll() is not None:
                continue
            process.kill()
            process.wait(timeout=10)


class WatchdogExistenceTests(unittest.TestCase):
    def test_watchdog_script_exists(self):
        self.assertTrue(
            WATCHDOG.is_file(),
            "the watchdog belongs to observe-inner-loop so it installs with "
            "the runtime",
        )

    def test_watchdog_prefers_pidfd_with_a_documented_fallback(self):
        source = WATCHDOG.read_text(encoding="utf-8")
        self.assertIn(
            "pidfd_open",
            source,
            "exit detection should be immediate, not a seconds-long poll",
        )
        self.assertIn("/proc/", source, "a fallback path must exist for old kernels")

    def test_watchdog_reads_no_session_or_credential_data(self):
        source = WATCHDOG.read_text(encoding="utf-8")
        for forbidden in ("apiKey", "auth.json", "models.json", "sessions/"):
            with self.subTest(token=forbidden):
                self.assertNotIn(forbidden, source)


class HeartbeatTests(unittest.TestCase):
    def setUp(self):
        self._directory = tempfile.TemporaryDirectory()
        self.fixture = WatchdogFixture(Path(self._directory.name))

    def tearDown(self):
        self.fixture.cleanup()
        self._directory.cleanup()

    def test_heartbeat_file_is_named_by_pid_role_and_depth(self):
        pid = self.fixture.start_monitored()
        self.fixture.start_watchdog(pid, role="planner", depth=0)
        record = wait_until(lambda: self.fixture.record(pid))
        self.assertIsNotNone(
            record, "the watchdog must refresh liveness/<pid>--<role>--<depth>.json"
        )
        self.assertEqual(record["pid"], pid)
        self.assertEqual(record["role"], "planner")
        self.assertEqual(record["depth"], 0)
        self.assertEqual(record["status"], "alive")

    def test_heartbeat_advances_while_the_process_lives(self):
        pid = self.fixture.start_monitored()
        self.fixture.start_watchdog(pid, interval="0.2")
        first = wait_until(lambda: self.fixture.record(pid))
        self.assertIsNotNone(first)
        second = wait_until(
            lambda: (
                record
                if (record := self.fixture.record(pid))
                and record["heartbeat_at"] != first["heartbeat_at"]
                else None
            )
        )
        self.assertIsNotNone(second, "the heartbeat must be refreshed, not written once")

    def test_no_business_event_is_emitted_while_alive(self):
        pid = self.fixture.start_monitored()
        self.fixture.start_watchdog(pid)
        wait_until(lambda: self.fixture.record(pid))
        self.assertEqual(
            self.fixture.event_kinds(),
            [],
            "liveness must not leak into the business event stream",
        )


class ExitDetectionTests(unittest.TestCase):
    def setUp(self):
        self._directory = tempfile.TemporaryDirectory()
        self.fixture = WatchdogFixture(Path(self._directory.name))

    def tearDown(self):
        self.fixture.cleanup()
        self._directory.cleanup()

    def _kill_and_wait(self, pid):
        os.kill(pid, signal.SIGKILL)
        self.fixture.monitored.wait(timeout=10)

    def test_sigkilled_process_gets_an_exit_receipt_from_the_monitor(self):
        """An in-process plugin could never write this record."""
        pid = self.fixture.start_monitored()
        self.fixture.start_watchdog(pid, depth=0)
        wait_until(lambda: self.fixture.record(pid))
        self._kill_and_wait(pid)
        record = wait_until(
            lambda: (
                value
                if (value := self.fixture.record(pid))
                and value["status"] == "exited"
                else None
            )
        )
        self.assertIsNotNone(record, "the watchdog must file the exit receipt")
        self.assertIn("exited_at", record)

    def test_depth_zero_exit_publishes_runner_exited(self):
        pid = self.fixture.start_monitored()
        self.fixture.start_watchdog(pid, depth=0)
        wait_until(lambda: self.fixture.record(pid))
        self._kill_and_wait(pid)
        kinds = wait_until(
            lambda: self.fixture.event_kinds() or None
        )
        self.assertEqual(
            kinds, ["runner_exited"], "a depth-0 exit is the only stop signal nobody else reports"
        )

    def test_depth_one_exit_stays_out_of_the_event_stream(self):
        pid = self.fixture.start_monitored()
        self.fixture.start_watchdog(pid, role="coder", depth=1)
        wait_until(lambda: self.fixture.record(pid, role="coder", depth=1))
        self._kill_and_wait(pid)
        record = wait_until(
            lambda: (
                value
                if (value := self.fixture.record(pid, role="coder", depth=1))
                and value["status"] == "exited"
                else None
            )
        )
        self.assertIsNotNone(record, "the exit is still recorded in liveness/")
        self.assertEqual(
            self.fixture.event_kinds(),
            [],
            "the parent delegation already observes a child's exit",
        )

    def test_watchdog_exits_after_the_monitored_process(self):
        """The watchdog is not a daemon; it lives and dies with its subject."""
        pid = self.fixture.start_monitored()
        watchdog = self.fixture.start_watchdog(pid)
        wait_until(lambda: self.fixture.record(pid))
        self._kill_and_wait(pid)
        self.assertIsNotNone(
            wait_until(lambda: watchdog.poll() is not None),
            "the watchdog must not outlive the process it monitors",
        )
        self.assertEqual(watchdog.returncode, 0)

    def test_already_dead_process_is_recorded_immediately(self):
        pid = self.fixture.start_monitored(seconds=0)
        self.fixture.monitored.wait(timeout=10)
        self.fixture.start_watchdog(pid, depth=0)
        record = wait_until(
            lambda: (
                value
                if (value := self.fixture.record(pid))
                and value["status"] == "exited"
                else None
            )
        )
        self.assertIsNotNone(record)


if __name__ == "__main__":
    unittest.main()
