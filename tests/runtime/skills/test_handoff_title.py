"""Requirement tests for registry titles (design S2.6, S3 item 8).

The Goal line the ``write-handoff`` contract already forces is the title:
the delegator produces it for free, so the common path needs no model call
at all. Compression is the exception, not the route — and it must be
asynchronous, separable, and allowed to fail, because the mechanical plane
may never block on the semantic plane. While ``title.txt`` is absent the
registry shows the truncated Goal.

All paths are relative to the repository root
``ROOT = Path(__file__).resolve().parents[3]``.
"""

import json
import os
import subprocess
import tempfile
import time
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
HANDOFF_CLI = (
    ROOT / ".teamflow" / "skills" / "write-handoff" / "scripts" / "handoff_state.py"
)
COMPRESSOR = (
    ROOT / ".teamflow" / "skills" / "write-handoff" / "scripts" / "compress_title.py"
)
AGENT = ROOT / ".teamflow" / "agents" / "title-compressor.md"

TIMEOUT = 60
RUN_ID = "run-20260101-000000-dddd"
TITLE_BUDGET = 80

LONG_GOAL = (
    "the registry must show a readable one-line summary even when the delegator "
    "wrote a very long and rambling goal sentence that nobody wants to read twice"
)


def clean_env(home: Path, **overrides) -> dict:
    env = dict(os.environ)
    for key in list(env):
        if key.startswith(("TEAMFLOW_", "WORKFLOW_", "OPENCODE_WORKFLOW_")):
            env.pop(key)
    env["HOME"] = str(home)
    env.update(overrides)
    return env


class TitleFixture:
    def __init__(self, directory: Path, **env_overrides):
        self.root = directory
        self.home = directory / "home"
        self.home.mkdir(parents=True, exist_ok=True)
        self.code = directory / ".teamflow" / "runs" / "code"
        self.code.mkdir(parents=True, exist_ok=True)
        self.env_overrides = env_overrides

    def cli(self, *args, body=None):
        return subprocess.run(
            ["python3", str(HANDOFF_CLI), *args, "--runs-dir", str(self.code),
             "--run-id", RUN_ID],
            cwd=str(self.root),
            input=body,
            env=clean_env(self.home, **self.env_overrides),
            capture_output=True,
            text=True,
            timeout=TIMEOUT,
        )

    def open_handoff(self, body, role="coder"):
        completed = self.cli(
            "handoff", "open", "--role", role, "--body-file", "-", body=body
        )
        assert completed.returncode == 0, completed.stderr
        return json.loads(completed.stdout)

    def title_of(self, handoff_id):
        rows = json.loads(self.cli("handoff", "list").stdout)
        for row in rows:
            if row["handoff_id"] == handoff_id:
                return row["title"]
        return None

    def title_file(self, handoff_id):
        return self.code / RUN_ID / "handoffs" / handoff_id / "title.txt"


def fake_model(directory: Path, text: str, delay: float = 0.0) -> Path:
    """A stand-in emitting the same JSON stream pi emits."""
    script = directory / "fake-model.sh"
    payload = json.dumps(
        {
            "type": "message_end",
            "message": {"role": "assistant", "content": [{"type": "text", "text": text}]},
        }
    )
    script.write_text(
        "#!/bin/sh\n"
        + (f"sleep {delay}\n" if delay else "")
        + f"cat <<'JSON'\n{payload}\nJSON\n",
        encoding="utf-8",
    )
    script.chmod(0o755)
    return script


class FreeTitleTests(unittest.TestCase):
    """The common path costs nothing: the Goal line is already one line."""

    def test_short_goal_becomes_the_title_without_a_model_call(self):
        with tempfile.TemporaryDirectory() as name:
            directory = Path(name)
            never = directory / "must-not-run.sh"
            never.write_text("#!/bin/sh\nexit 17\n", encoding="utf-8")
            never.chmod(0o755)
            fixture = TitleFixture(
                directory, TEAMFLOW_TITLE_COMPRESS_CMD=str(never)
            )
            opened = fixture.open_handoff("- Goal: add a health check endpoint.\n")
            self.assertEqual(fixture.title_of(opened["handoff_id"]), "add a health check endpoint.")
            self.assertFalse(
                fixture.title_file(opened["handoff_id"]).exists(),
                "a Goal within budget must not trigger compression",
            )

    def test_explicit_title_overrides_the_goal(self):
        with tempfile.TemporaryDirectory() as name:
            fixture = TitleFixture(Path(name))
            completed = fixture.cli(
                "handoff", "open", "--role", "coder", "--body-file", "-",
                "--title", "explicit board title",
                body="- Goal: something else entirely.\n",
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            opened = json.loads(completed.stdout)
            self.assertEqual(fixture.title_of(opened["handoff_id"]), "explicit board title")


class DegradationTests(unittest.TestCase):
    """A failed or pending compression must never leave the board blank."""

    def test_long_goal_degrades_to_a_truncated_goal(self):
        with tempfile.TemporaryDirectory() as name:
            directory = Path(name)
            failing = directory / "failing.sh"
            failing.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
            failing.chmod(0o755)
            fixture = TitleFixture(
                directory, TEAMFLOW_TITLE_COMPRESS_CMD=str(failing)
            )
            opened = fixture.open_handoff(f"- Goal: {LONG_GOAL}\n")
            title = fixture.title_of(opened["handoff_id"])
            self.assertLessEqual(len(title), TITLE_BUDGET)
            self.assertTrue(
                LONG_GOAL.startswith(title.rstrip("\u2026")),
                f"the degraded title must be the Goal's own prefix, got {title!r}",
            )

    def test_state_write_does_not_wait_for_the_model(self):
        with tempfile.TemporaryDirectory() as name:
            directory = Path(name)
            slow = fake_model(directory, "compressed title", delay=30)
            fixture = TitleFixture(directory, TEAMFLOW_TITLE_COMPRESS_CMD=str(slow))
            started = time.monotonic()
            opened = fixture.open_handoff(f"- Goal: {LONG_GOAL}\n")
            elapsed = time.monotonic() - started
            self.assertLess(
                elapsed, 10,
                "open must return immediately; compression is detached",
            )
            state = json.loads(
                (
                    fixture.code / RUN_ID / "handoffs" / opened["handoff_id"] / "state.json"
                ).read_text(encoding="utf-8")
            )
            self.assertEqual(state["status"], "open")

    def test_missing_goal_still_yields_a_row(self):
        with tempfile.TemporaryDirectory() as name:
            directory = Path(name)
            failing = directory / "failing.sh"
            failing.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
            failing.chmod(0o755)
            fixture = TitleFixture(directory, TEAMFLOW_TITLE_COMPRESS_CMD=str(failing))
            opened = fixture.open_handoff("- Scope: a/b.py\n- Acceptance: 1. x\n")
            self.assertEqual(fixture.title_of(opened["handoff_id"]), "")


class CompressorTests(unittest.TestCase):
    """The compressor writes title.txt or nothing at all."""

    def _handoff(self, fixture, body):
        return fixture.open_handoff(body)

    def _run_compressor(self, fixture, handoff_id, command):
        directory = fixture.code / RUN_ID / "handoffs" / handoff_id
        return subprocess.run(
            [
                "python3", str(COMPRESSOR),
                "--handoff-dir", str(directory),
                "--budget", str(TITLE_BUDGET),
            ],
            cwd=str(fixture.root),
            env=clean_env(fixture.home, TEAMFLOW_TITLE_COMPRESS_CMD=str(command)),
            capture_output=True,
            text=True,
            timeout=TIMEOUT,
        )

    def test_compressor_exists_and_has_an_agent(self):
        self.assertTrue(COMPRESSOR.is_file())
        self.assertTrue(AGENT.is_file(), "the compressor needs a cheap-model agent")
        text = AGENT.read_text(encoding="utf-8")
        self.assertIn(
            "mimo",
            text,
            "titles use the same cheap model as command/supervisor",
        )

    def test_compressed_title_is_written_and_used(self):
        with tempfile.TemporaryDirectory() as name:
            directory = Path(name)
            fixture = TitleFixture(directory)
            opened = self._handoff(fixture, f"- Goal: {LONG_GOAL}\n")
            model = fake_model(directory, "readable one-line board summary")
            completed = self._run_compressor(fixture, opened["handoff_id"], model)
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertEqual(
                fixture.title_file(opened["handoff_id"]).read_text(encoding="utf-8").strip(),
                "readable one-line board summary",
            )
            self.assertEqual(
                fixture.title_of(opened["handoff_id"]), "readable one-line board summary"
            )

    def test_multiline_model_output_is_reduced_to_one_line(self):
        with tempfile.TemporaryDirectory() as name:
            directory = Path(name)
            fixture = TitleFixture(directory)
            opened = self._handoff(fixture, f"- Goal: {LONG_GOAL}\n")
            model = fake_model(directory, "first line title\nsecond line commentary")
            self._run_compressor(fixture, opened["handoff_id"], model)
            self.assertEqual(
                fixture.title_of(opened["handoff_id"]), "first line title"
            )

    def test_overlong_model_output_is_truncated_to_the_budget(self):
        with tempfile.TemporaryDirectory() as name:
            directory = Path(name)
            fixture = TitleFixture(directory)
            opened = self._handoff(fixture, f"- Goal: {LONG_GOAL}\n")
            model = fake_model(directory, "T" * 400)
            self._run_compressor(fixture, opened["handoff_id"], model)
            self.assertLessEqual(
                len(fixture.title_of(opened["handoff_id"])), TITLE_BUDGET
            )

    def test_failing_model_writes_nothing(self):
        with tempfile.TemporaryDirectory() as name:
            directory = Path(name)
            fixture = TitleFixture(directory)
            opened = self._handoff(fixture, f"- Goal: {LONG_GOAL}\n")
            failing = directory / "failing.sh"
            failing.write_text("#!/bin/sh\nexit 3\n", encoding="utf-8")
            failing.chmod(0o755)
            completed = self._run_compressor(fixture, opened["handoff_id"], failing)
            self.assertEqual(
                completed.returncode, 0,
                "a failed compression is not an error for the caller",
            )
            self.assertFalse(fixture.title_file(opened["handoff_id"]).exists())

    def test_empty_model_output_writes_nothing(self):
        with tempfile.TemporaryDirectory() as name:
            directory = Path(name)
            fixture = TitleFixture(directory)
            opened = self._handoff(fixture, f"- Goal: {LONG_GOAL}\n")
            model = fake_model(directory, "   ")
            self._run_compressor(fixture, opened["handoff_id"], model)
            self.assertFalse(fixture.title_file(opened["handoff_id"]).exists())

    def test_compressor_never_touches_state(self):
        with tempfile.TemporaryDirectory() as name:
            directory = Path(name)
            fixture = TitleFixture(directory)
            opened = self._handoff(fixture, f"- Goal: {LONG_GOAL}\n")
            state_path = (
                fixture.code / RUN_ID / "handoffs" / opened["handoff_id"] / "state.json"
            )
            before = state_path.read_bytes()
            model = fake_model(directory, "a title")
            self._run_compressor(fixture, opened["handoff_id"], model)
            self.assertEqual(
                state_path.read_bytes(), before,
                "the semantic plane may not modify mechanical state",
            )


if __name__ == "__main__":
    unittest.main()
