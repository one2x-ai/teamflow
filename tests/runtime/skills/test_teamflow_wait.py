"""Requirement tests for ``teamflow wait`` (design S2.9).

The 30-second polling ladder (probe -> phase status -> session list) cost a
tool call plus a result on every tick, even when nothing had changed, and
each one broke the outer loop's KV cache. ``teamflow wait`` replaces the
whole ladder with one blocking call: it returns when something happens or
when the timeout expires, and ``--since`` makes reconnecting idempotent.

The listener reads *file names*, not bodies: the name carries the sequence,
subject, kind, and status, which is all the outer loop needs to decide
whether to look closer.

All paths are relative to the repository root
``ROOT = Path(__file__).resolve().parents[3]``.
"""

import json
import os
import subprocess
import tempfile
import threading
import time
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
WAIT_CLI = ROOT / ".teamflow" / "skills" / "observe-inner-loop" / "scripts" / "wait.py"
HANDOFF_CLI = (
    ROOT / ".teamflow" / "skills" / "write-handoff" / "scripts" / "handoff_state.py"
)
TEAMFLOW_BIN = ROOT / ".teamflow" / "bin" / "teamflow"

TIMEOUT = 90
RUN_ID = "run-20260101-000000-aaaa"

HANDOFF_BODY = "- Goal: wait returns without polling.\n- Scope: a/b.py\n"


def clean_env(home: Path, **overrides) -> dict:
    env = dict(os.environ)
    for key in list(env):
        if key.startswith(("TEAMFLOW_", "WORKFLOW_", "OPENCODE_WORKFLOW_")):
            env.pop(key)
    env["HOME"] = str(home)
    env.update(overrides)
    return env


class WaitFixture:
    def __init__(self, directory: Path, backend="auto"):
        self.root = directory
        self.home = directory / "home"
        self.home.mkdir(parents=True, exist_ok=True)
        self.code = directory / ".teamflow" / "runs" / "code"
        self.code.mkdir(parents=True, exist_ok=True)
        self.backend = backend

    def env(self):
        return clean_env(self.home, TEAMFLOW_WAIT_BACKEND=self.backend)

    def open_handoff(self, role="coder"):
        completed = subprocess.run(
            [
                "python3", str(HANDOFF_CLI), "handoff", "open",
                "--runs-dir", str(self.code), "--run-id", RUN_ID,
                "--role", role, "--body-file", "-",
            ],
            cwd=str(self.root),
            input=HANDOFF_BODY,
            env=clean_env(self.home),
            capture_output=True,
            text=True,
            timeout=TIMEOUT,
        )
        assert completed.returncode == 0, completed.stderr
        return json.loads(completed.stdout)

    def wait(self, *extra, run_id=RUN_ID, timeout="2"):
        argv = [
            "python3", str(WAIT_CLI), "--runs-dir", str(self.code),
            "--timeout", timeout, *extra,
        ]
        if run_id:
            argv += ["--run-id", run_id]
        completed = subprocess.run(
            argv,
            cwd=str(self.root),
            env=self.env(),
            capture_output=True,
            text=True,
            timeout=TIMEOUT,
        )
        assert completed.returncode == 0, completed.stderr
        return json.loads(completed.stdout), completed


class WaitExistenceTests(unittest.TestCase):
    def test_wait_script_exists(self):
        self.assertTrue(
            WAIT_CLI.is_file(),
            "wait.py belongs to the observe-inner-loop skill so it installs "
            "with the runtime",
        )

    def test_teamflow_dispatches_wait(self):
        self.assertIn("wait.py", TEAMFLOW_BIN.read_text(encoding="utf-8"))

    def test_source_uses_inotify_with_a_documented_fallback(self):
        source = WAIT_CLI.read_text(encoding="utf-8")
        self.assertIn(
            "inotify", source, "Linux must use inotify rather than busy polling"
        )
        self.assertIn(
            "IN_MOVED_TO",
            source,
            "only a completed rename is a delivered event; a create is not",
        )
        self.assertRegex(
            source,
            r"(?i)fall ?back|degrade",
            "the fallback must be documented so degradation stays transparent",
        )


class WaitReturnsExistingEventsTests(unittest.TestCase):
    def test_returns_events_already_on_disk(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = WaitFixture(Path(directory))
            opened = fixture.open_handoff()
            payload, _ = fixture.wait()
            self.assertEqual(len(payload["events"]), 1, payload)
            event = payload["events"][0]
            self.assertEqual(event["seq"], 1)
            self.assertEqual(event["subject"], opened["handoff_id"])
            self.assertEqual(event["kind"], "handoff_opened")
            self.assertEqual(event["status"], "OPEN")
            self.assertEqual(payload["seq"], 1, "seq is the water mark")

    def test_fields_come_from_the_file_name_not_the_body(self):
        """Reading a body is the exception; the name answers the question."""
        with tempfile.TemporaryDirectory() as directory:
            fixture = WaitFixture(Path(directory))
            fixture.open_handoff()
            events = fixture.code / RUN_ID / "events"
            name = next(iter(sorted(p.name for p in events.iterdir())))
            (events / name).write_text("", encoding="utf-8")
            payload, _ = fixture.wait()
            self.assertEqual(payload["events"][0]["kind"], "handoff_opened")
            self.assertEqual(payload["events"][0]["file"], f"events/{name}")

    def test_since_returns_only_the_increment(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = WaitFixture(Path(directory))
            fixture.open_handoff(role="coder")
            fixture.open_handoff(role="test-runner")
            payload, _ = fixture.wait("--since", "1")
            self.assertEqual([event["seq"] for event in payload["events"]], [2])

    def test_since_at_the_water_mark_is_idempotent(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = WaitFixture(Path(directory))
            fixture.open_handoff()
            first, _ = fixture.wait()
            second, _ = fixture.wait("--since", str(first["seq"]))
            self.assertEqual(second["events"], [], "a reconnect must not replay")
            self.assertEqual(second["seq"], first["seq"])

    def test_kind_filter_selects_only_requested_kinds(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = WaitFixture(Path(directory))
            opened = fixture.open_handoff()
            subprocess.run(
                [
                    "python3", str(HANDOFF_CLI), "handoff", "finish",
                    "--runs-dir", str(fixture.code), "--run-id", RUN_ID,
                    "--id", opened["handoff_id"], "--status", "PASS",
                    "--summary", "s",
                ],
                cwd=str(fixture.root), env=clean_env(fixture.home),
                capture_output=True, text=True, timeout=TIMEOUT, check=True,
            )
            payload, _ = fixture.wait("--kind", "handoff_finished")
            self.assertEqual(
                [event["kind"] for event in payload["events"]], ["handoff_finished"]
            )
            self.assertEqual(
                payload["seq"], 3,
                "the water mark must track every event, not just matching ones",
            )

    def test_events_are_returned_in_sequence_order(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = WaitFixture(Path(directory))
            for _ in range(3):
                fixture.open_handoff()
            payload, _ = fixture.wait()
            self.assertEqual([event["seq"] for event in payload["events"]], [1, 2, 3])


class WaitTimeoutTests(unittest.TestCase):
    def test_timeout_with_no_events_returns_an_empty_array_and_the_water_mark(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = WaitFixture(Path(directory))
            started = time.monotonic()
            payload, completed = fixture.wait(timeout="1")
            elapsed = time.monotonic() - started
            self.assertEqual(payload["events"], [])
            self.assertEqual(payload["seq"], 0)
            self.assertGreaterEqual(
                elapsed, 0.8, "wait must actually block for the timeout"
            )
            self.assertEqual(
                completed.returncode, 0,
                "an empty result is normal, not a failure; emptiness is in the JSON",
            )

    def test_silence_is_not_reported_as_a_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = WaitFixture(Path(directory))
            _, completed = fixture.wait(timeout="1")
            self.assertEqual(completed.stderr.strip(), "")

    def test_returns_as_soon_as_an_event_is_delivered(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = WaitFixture(Path(directory))
            # Prime the directories so the watch has something to attach to.
            fixture.open_handoff()
            baseline, _ = fixture.wait()

            def deliver():
                time.sleep(0.7)
                fixture.open_handoff(role="test-writer")

            worker = threading.Thread(target=deliver)
            started = time.monotonic()
            worker.start()
            payload, _ = fixture.wait("--since", str(baseline["seq"]), timeout="20")
            elapsed = time.monotonic() - started
            worker.join()
            self.assertEqual(len(payload["events"]), 1, payload)
            self.assertLess(
                elapsed, 15,
                "wait must wake on delivery instead of sitting out the timeout",
            )


class WaitBackendEquivalenceTests(unittest.TestCase):
    """Degradation must be invisible to the caller."""

    def _observe(self, backend):
        with tempfile.TemporaryDirectory() as directory:
            fixture = WaitFixture(Path(directory), backend=backend)
            fixture.open_handoff()
            payload, _ = fixture.wait()
            return payload

    def test_poll_backend_matches_inotify_backend(self):
        inotify = self._observe("inotify")
        polled = self._observe("poll")
        self.assertEqual(inotify["events"][0]["kind"], polled["events"][0]["kind"])
        self.assertEqual(inotify["seq"], polled["seq"])

    def test_poll_backend_also_wakes_on_delivery(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = WaitFixture(Path(directory), backend="poll")
            fixture.open_handoff()
            baseline, _ = fixture.wait()

            def deliver():
                time.sleep(0.5)
                fixture.open_handoff(role="test-writer")

            worker = threading.Thread(target=deliver)
            started = time.monotonic()
            worker.start()
            payload, _ = fixture.wait("--since", str(baseline["seq"]), timeout="20")
            elapsed = time.monotonic() - started
            worker.join()
            self.assertEqual(len(payload["events"]), 1)
            self.assertLess(elapsed, 15)


class WaitOutputStabilityTests(unittest.TestCase):
    """Byte-stable output protects the outer loop's KV cache."""

    def test_repeated_calls_produce_identical_bytes(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = WaitFixture(Path(directory))
            fixture.open_handoff()
            first = fixture.wait()[1].stdout
            second = fixture.wait()[1].stdout
            self.assertEqual(first, second)

    def test_key_order_is_fixed(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = WaitFixture(Path(directory))
            fixture.open_handoff()
            raw = fixture.wait()[1].stdout
            payload = json.loads(raw)
            self.assertEqual(list(payload.keys()), ["run_id", "seq", "events"])
            self.assertEqual(
                list(payload["events"][0].keys()),
                ["seq", "subject", "kind", "status", "file"],
            )

    def test_output_carries_no_session_or_prompt_data(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = WaitFixture(Path(directory))
            fixture.open_handoff()
            raw = fixture.wait()[1].stdout
            for forbidden in ("prompt", "reasoning", "apiKey", "session"):
                with self.subTest(token=forbidden):
                    self.assertNotIn(forbidden, raw)


class WaitSpoolDiscoveryTests(unittest.TestCase):
    """S2.7: without a run-id, wait discovers runs from the shared spool."""

    def test_spool_mode_reports_run_level_events(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = WaitFixture(Path(directory))
            subprocess.run(
                [
                    "python3", str(HANDOFF_CLI), "handoff", "run-start",
                    "--runs-dir", str(fixture.code), "--run-id", RUN_ID,
                    "--role", "planner", "--pid", str(os.getpid()),
                ],
                cwd=str(fixture.root), env=clean_env(fixture.home),
                capture_output=True, text=True, timeout=TIMEOUT, check=True,
            )
            payload, _ = fixture.wait(run_id=None)
            self.assertIsNone(payload["run_id"])
            self.assertEqual(
                [event["kind"] for event in payload["events"]], ["run_started"]
            )
            self.assertEqual(payload["events"][0]["subject"], RUN_ID)
            self.assertTrue(payload["events"][0]["file"].startswith("_spool/"))

    def test_spool_mode_ignores_the_staging_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = WaitFixture(Path(directory))
            (fixture.code / "_spool" / "tmp").mkdir(parents=True)
            (fixture.code / "_spool" / ".events.seq").write_text("0", encoding="utf-8")
            payload, _ = fixture.wait(run_id=None, timeout="1")
            self.assertEqual(payload["events"], [])

    def test_missing_run_directory_is_not_an_error(self):
        """The outer loop may start waiting before the inner loop writes."""
        with tempfile.TemporaryDirectory() as directory:
            fixture = WaitFixture(Path(directory))
            payload, completed = fixture.wait(run_id="run-does-not-exist", timeout="1")
            self.assertEqual(payload["events"], [])
            self.assertEqual(completed.returncode, 0)


if __name__ == "__main__":
    unittest.main()
