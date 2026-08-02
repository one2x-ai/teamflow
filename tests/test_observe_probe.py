"""Requirement tests for the outer-loop liveness probe (Part A).

The probe is a cheap, stdlib-only Python script that resolves the current
phase receipt and prints exactly one line of metadata.  It is the cheapest
rung on the observe-inner-loop escalation ladder: before spending tokens on
``teamflow phase status`` or ``teamflow session list``, the outer loop can
poll this single line.
"""

import json
import os
import subprocess
import tempfile
import time
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PROBE = ROOT / ".teamflow" / "skills" / "observe-inner-loop" / "scripts" / "probe.py"
TEAMFLOW_BIN = ROOT / ".teamflow" / "bin" / "teamflow"

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


def clean_env(home):
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


def _make_run_dir(tmp, run_id, status):
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


def _run_probe(tmp, run_id, home, pid=None, pi_running=None):
    """Invoke probe.py and return ``(stdout_text, returncode)``.

    When *run_id* is ``None`` the probe is invoked without ``--run-id``
    (discovery mode).  *pi_running* controls the ``TEAMFLOW_PROBE_PI_RUNNING``
    env var: ``None`` = unset (real ps check), ``"1"`` = force pi-present,
    ``"0"`` = force pi-absent.
    """
    cmd = ["python3", str(PROBE), "--runs-dir", str(tmp)]
    if run_id is not None:
        cmd += ["--run-id", run_id]
    if pid is not None:
        cmd += ["--pid", str(pid)]
    env = clean_env(home)
    if pi_running is not None:
        env["TEAMFLOW_PROBE_PI_RUNNING"] = pi_running
    proc = subprocess.run(
        cmd,
        cwd=str(ROOT),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
        timeout=PROBE_TIMEOUT,
    )
    return proc.stdout.decode("utf-8", "replace"), proc.returncode


def parse_line(line):
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
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            home = tmp / "home"
            home.mkdir()
            _make_run_dir(tmp, "r1", "RUNNING")
            out, rc = _run_probe(tmp, "r1", home, pi_running="1")
            lines = out.splitlines()
            self.assertEqual(len(lines), 1, "probe must print exactly one line")
            fields = parse_line(lines[0])
            self.assertEqual(
                set(fields.keys()),
                ALLOWED_KEYS,
                f"probe keys must be exactly {ALLOWED_KEYS}, got {set(fields.keys())}",
            )
            self.assertEqual(fields["state"], "alive")
            self.assertEqual(rc, 0, "alive must exit 0")
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
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            home = tmp / "home"
            home.mkdir()
            _make_run_dir(tmp, "r2", "PASS")
            out, rc = _run_probe(tmp, "r2", home)
            fields = parse_line(out.strip())
            self.assertEqual(fields["state"], "exited")
            self.assertEqual(rc, 1, "exited must exit 1")

    def test_missing_run_emits_unknown(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            home = tmp / "home"
            home.mkdir()
            out, rc = _run_probe(tmp, "nonexistent-run", home)
            fields = parse_line(out.strip())
            self.assertEqual(fields["state"], "unknown")
            self.assertEqual(rc, 2, "unknown must exit 2")

    def test_dead_pid_against_running_emits_exited(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            home = tmp / "home"
            home.mkdir()
            _make_run_dir(tmp, "r3", "RUNNING")
            out, rc = _run_probe(tmp, "r3", home, pid=DEAD_PID)
            fields = parse_line(out.strip())
            self.assertEqual(fields["state"], "exited")
            self.assertEqual(rc, 1, "exited must exit 1")


class ProbeDiscoveryTests(unittest.TestCase):
    """P1/P6: zero-argument discovery and ambiguity reporting."""

    def test_discovery_finds_newest_run_by_dir_mtime(self):
        """P1: with no --run-id, the newest run dir is auto-selected."""
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            home = tmp / "home"
            home.mkdir()
            _make_run_dir(tmp, "alpha", "RUNNING")
            _make_run_dir(tmp, "beta", "RUNNING")
            # Make 'alpha' older so 'beta' is the newest.
            old = time.time() - 600
            os.utime(tmp / "alpha", (old, old))
            os.utime(tmp / "beta", None)
            out, rc = _run_probe(tmp, None, home, pi_running="1")
            fields = parse_line(out.strip())
            self.assertEqual(set(fields.keys()), ALLOWED_KEYS)
            self.assertEqual(fields["state"], "alive")
            # beta has phase 'p' status RUNNING; discovery should resolve it.
            self.assertTrue(
                fields["fp"].startswith("p:RUNNING:"),
                f"discovery should resolve the newest run, got fp={fields.get('fp')!r}",
            )
            self.assertEqual(rc, 0)

    def test_discovery_no_subdirs_emits_unknown(self):
        """P6: an empty runs-dir is ambiguous — never guess."""
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            home = tmp / "home"
            home.mkdir()
            out, rc = _run_probe(tmp, None, home)
            fields = parse_line(out.strip())
            self.assertEqual(fields["state"], "unknown")
            self.assertEqual(fields["activity"], "-")
            self.assertEqual(fields["fp"], "-")
            self.assertEqual(rc, 2)

    def test_run_id_overrides_discovery(self):
        """P1: explicit --run-id pins a specific run, bypassing discovery."""
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            home = tmp / "home"
            home.mkdir()
            _make_run_dir(tmp, "alpha", "RUNNING")
            _make_run_dir(tmp, "beta", "PASS")
            # beta is newer, but we pin alpha.
            old = time.time() - 600
            os.utime(tmp / "alpha", (old, old))
            os.utime(tmp / "beta", None)
            out, rc = _run_probe(tmp, "alpha", home, pi_running="1")
            fields = parse_line(out.strip())
            self.assertEqual(fields["state"], "alive")
            self.assertTrue(fields["fp"].startswith("p:RUNNING:"))
            self.assertEqual(rc, 0)


class ProbePidFreeLivenessTests(unittest.TestCase):
    """P2: liveness derived from the process table when --pid is omitted."""

    def test_running_pi_present_emits_alive(self):
        """P2: RUNNING + pi process present → alive."""
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            home = tmp / "home"
            home.mkdir()
            _make_run_dir(tmp, "rp1", "RUNNING")
            out, rc = _run_probe(tmp, "rp1", home, pi_running="1")
            fields = parse_line(out.strip())
            self.assertEqual(fields["state"], "alive")
            self.assertEqual(rc, 0)

    def test_running_pi_absent_emits_exited(self):
        """P2: RUNNING + no pi process → exited (crashed)."""
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            home = tmp / "home"
            home.mkdir()
            _make_run_dir(tmp, "rp2", "RUNNING")
            out, rc = _run_probe(tmp, "rp2", home, pi_running="0")
            fields = parse_line(out.strip())
            self.assertEqual(fields["state"], "exited")
            self.assertEqual(rc, 1)

    def test_pass_regardless_of_pi_emits_exited(self):
        """P2: terminal status (PASS) → exited regardless of pi presence."""
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            home = tmp / "home"
            home.mkdir()
            _make_run_dir(tmp, "rp3", "PASS")
            out, rc = _run_probe(tmp, "rp3", home, pi_running="1")
            fields = parse_line(out.strip())
            self.assertEqual(fields["state"], "exited")
            self.assertEqual(rc, 1)


class ProbeExitCodeTests(unittest.TestCase):
    """P4: exit codes 0/1/2 usable without parsing output."""

    def test_alive_exits_zero(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            home = tmp / "home"
            home.mkdir()
            _make_run_dir(tmp, "ec1", "RUNNING")
            _, rc = _run_probe(tmp, "ec1", home, pi_running="1")
            self.assertEqual(rc, 0)

    def test_exited_exits_one(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            home = tmp / "home"
            home.mkdir()
            _make_run_dir(tmp, "ec2", "PASS")
            _, rc = _run_probe(tmp, "ec2", home)
            self.assertEqual(rc, 1)

    def test_unknown_exits_two(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            home = tmp / "home"
            home.mkdir()
            _, rc = _run_probe(tmp, "no-such-run", home)
            self.assertEqual(rc, 2)


class ProbeSubcommandTests(unittest.TestCase):
    """P5: ``teamflow probe`` dispatches to probe.py as a single command."""

    def test_teamflow_probe_produces_single_line(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            home = tmp / "home"
            home.mkdir()
            _make_run_dir(tmp, "sub1", "RUNNING")
            env = clean_env(home)
            env["TEAMFLOW_PROBE_PI_RUNNING"] = "1"
            proc = subprocess.run(
                ["bash", str(TEAMFLOW_BIN), "probe",
                 "--runs-dir", str(tmp), "--run-id", "sub1"],
                cwd=str(ROOT),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=env,
                timeout=PROBE_TIMEOUT,
            )
            out = proc.stdout.decode("utf-8", "replace")
            lines = out.splitlines()
            self.assertEqual(len(lines), 1,
                             "teamflow probe must emit exactly one line")
            self.assertIn("state=", lines[0])
            self.assertEqual(proc.returncode, 0,
                             "alive via subcommand must exit 0")


class ProbeOutputIsolationTests(unittest.TestCase):
    """Probe output must never leak inner-loop data nouns."""

    def test_output_contains_no_inner_loop_nouns(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            home = tmp / "home"
            home.mkdir()
            _make_run_dir(tmp, "r4", "RUNNING")
            out, _ = _run_probe(tmp, "r4", home, pi_running="1")
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
