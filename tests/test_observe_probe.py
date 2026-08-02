"""Requirement tests for the outer-loop liveness probe (Part A).

The probe is a cheap, stdlib-only Python script that resolves the current
phase receipt and prints exactly one line of metadata.  It is the cheapest
rung on the observe-inner-loop escalation ladder: before spending tokens on
``teamflow phase status`` or ``teamflow session list``, the outer loop can
poll this single line.

These tests assert the *desired end state* and must currently FAIL RED
because ``probe.py`` does not exist yet.
"""

import json
import os
import subprocess
import time
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PROBE = ROOT / ".teamflow" / "skills" / "observe-inner-loop" / "scripts" / "probe.py"

DEAD_PID = 999999
PROBE_TIMEOUT = 30

# Keys the probe is permitted to emit — nothing else.
ALLOWED_KEYS = {"state", "activity", "fp"}

# The probe must be isolated from inner-loop data: neither its output nor its
# source may reference these substrings.
FORBIDDEN_OUTPUT = ("turn", "tool", "event", "message", "prompt", "session")
FORBIDDEN_SOURCE = (
    "session",
    "prompt",
    "reasoning",
    "response",
    "credential",
    "auth",
    "apiKey",
    "opencode.db",
    "models.json",
)


def clean_env(home: Path) -> dict:
    """Return a proxy-stripped, HOME-isolated copy of os.environ."""
    env = dict(os.environ)
    for key in (
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "ALL_PROXY",
        "http_proxy",
        "https_proxy",
        "all_proxy",
    ):
        env.pop(key, None)
    env["HOME"] = str(home)
    return env


def _make_run_dir(tmp: Path, run_id: str, status: str) -> Path:
    """Build a fixture run directory with current.json + a phase JSON."""
    run_dir = tmp / run_id
    phase_dir = run_dir / "phases"
    phase_dir.mkdir(parents=True, exist_ok=True)

    phase_file = phase_dir / "p.json"
    phase_file.write_text(
        json.dumps({"phase": "p", "status": status, "started_at": "2026-01-01T00:00:00Z"}),
        encoding="utf-8",
    )
    # Give the file a fresh mtime so activity is a small non-negative number.
    os.utime(phase_file, None)

    current = run_dir / "current.json"
    current.write_text(
        json.dumps({"phase": "p", "path": str(phase_file.resolve())}),
        encoding="utf-8",
    )
    return run_dir


def _run_probe(tmp: Path, run_id: str, home: Path, pid=None) -> str:
    """Invoke probe.py against *tmp* as runs-dir and return stdout text."""
    cmd = ["python3", str(PROBE), "--runs-dir", str(tmp), "--run-id", run_id]
    if pid is not None:
        cmd += ["--pid", str(pid)]
    proc = subprocess.run(
        cmd,
        cwd=str(ROOT),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=clean_env(home),
        timeout=PROBE_TIMEOUT,
    )
    return proc.stdout.decode("utf-8", "replace")


def parse_line(line: str) -> dict:
    """Parse ``key=value`` tokens separated by whitespace into a dict."""
    result = {}
    for token in line.split():
        if "=" in token:
            k, _, v = token.partition("=")
            result[k] = v
    return result


class ProbeExistenceTests(unittest.TestCase):
    def test_probe_exists_and_is_executable(self):
        self.assertTrue(
            PROBE.is_file(),
            "probe.py must exist at .teamflow/skills/observe-inner-loop/scripts/probe.py",
        )
        self.assertTrue(
            os.access(str(PROBE), os.X_OK),
            "probe.py must be executable (chmod +x)",
        )


class ProbeOutputContractTests(unittest.TestCase):
    """The probe emits exactly one line with only the documented fields."""

    def test_running_fixture_emits_alive_single_line(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            home = tmp / "home"
            home.mkdir()
            _make_run_dir(tmp, "r1", "RUNNING")
            out = _run_probe(tmp, "r1", home)
            lines = out.splitlines()
            self.assertEqual(len(lines), 1, "probe must print exactly one line")
            fields = parse_line(lines[0])
            self.assertEqual(
                set(fields.keys()),
                ALLOWED_KEYS,
                f"probe keys must be exactly {ALLOWED_KEYS}, got {set(fields.keys())}",
            )
            self.assertEqual(fields["state"], "alive")
            self.assertTrue(
                fields["fp"].startswith("p:RUNNING:"),
                f"fp must start with 'p:RUNNING:', got {fields.get('fp')!r}",
            )
            activity = fields["activity"]
            self.assertTrue(activity.endswith("s"), f"activity must end with 's', got {activity!r}")
            self.assertGreaterEqual(
                int(activity[:-1]), 0, "activity must be a non-negative integer"
            )

    def test_pass_fixture_emits_exited(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            home = tmp / "home"
            home.mkdir()
            _make_run_dir(tmp, "r2", "PASS")
            out = _run_probe(tmp, "r2", home)
            fields = parse_line(out.strip())
            self.assertEqual(fields["state"], "exited")

    def test_missing_run_emits_unknown(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            home = tmp / "home"
            home.mkdir()
            out = _run_probe(tmp, "nonexistent-run", home)
            fields = parse_line(out.strip())
            self.assertEqual(fields["state"], "unknown")

    def test_dead_pid_against_running_emits_exited(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            home = tmp / "home"
            home.mkdir()
            _make_run_dir(tmp, "r3", "RUNNING")
            out = _run_probe(tmp, "r3", home, pid=DEAD_PID)
            fields = parse_line(out.strip())
            self.assertEqual(fields["state"], "exited")


class ProbeOutputIsolationTests(unittest.TestCase):
    """Probe output must never leak inner-loop data nouns."""

    def test_output_contains_no_inner_loop_nouns(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            home = tmp / "home"
            home.mkdir()
            _make_run_dir(tmp, "r4", "RUNNING")
            out = _run_probe(tmp, "r4", home)
            for token in FORBIDDEN_OUTPUT:
                with self.subTest(token=token):
                    self.assertNotIn(token, out, f"probe output must not contain {token!r}")


class ProbeSourceIsolationTests(unittest.TestCase):
    """probe.py source must not reference session/prompt/secret data."""

    def test_source_contains_no_forbidden_substrings(self):
        self.assertTrue(PROBE.is_file(), "probe.py must exist for source inspection")
        source = PROBE.read_text(encoding="utf-8")
        for token in FORBIDDEN_SOURCE:
            with self.subTest(token=token):
                self.assertNotIn(
                    token,
                    source,
                    f"probe.py source must not contain {token!r}",
                )


if __name__ == "__main__":
    unittest.main()
