"""Requirement tests for the one-line run probe.

The probe is a cheap, stdlib-only Python script that resolves a run's
current handoff receipt and prints exactly one line of metadata. Since the
handoff refactor it is a *manual* diagnostic rather than a rung on a
polling ladder — observe-loop observation is ``teamflow wait``, which blocks
instead of polling — so its job is to answer "is this run alive, and what
is it on?" in one line a human can read.

Two facts it must respect: liveness comes from the process and never from a
receipt's status, and when the run recorded its depth-0 pid the check is
attributed to that pid instead of guessing from the process table.
"""

import json
import os
import shutil
import subprocess
import tempfile
import time
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PROBE = ROOT / ".teamflow" / "skills" / "observer" / "scripts" / "probe.py"
TEAMFLOW_BIN = ROOT / ".teamflow" / "bin" / "teamflow"

DEAD_PID = 999999
PROBE_TIMEOUT = 30

# Keys the probe is permitted to emit — nothing else.
ALLOWED_KEYS = {"state", "activity", "fp"}

# The probe must be isolated from execute-loop data: neither its output nor its
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


#: The handoff statuses a receipt can hold, and the token the probe prints.
HANDOFF_STATE = {
    "RUNNING": {"status": "running"},
    "PASS": {"status": "done", "result": "PASS"},
    "FAIL": {"status": "done", "result": "FAIL"},
    "BLOCKED": {"status": "blocked", "blocked": {"reason": "PROVIDER_FAILURE"}},
}


def runs_dir(tmp):
    """The runs directory, kept separate from the fixture's isolated HOME.

    Discovery treats every subdirectory as a run, so an unrelated directory
    sharing the parent would compete with the fixtures for "newest".
    """
    directory = tmp / "code"
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def _make_run_dir(tmp, run_id, status, handoff_id="h00001-planner", runner_pid=None):
    """Build a fixture run directory in the handoff layout.

    An in-flight handoff also leaves an ``active/`` sentinel; a terminal one
    does not, which is how the probe tells "on it" from "was on it".
    """
    run_dir = runs_dir(tmp) / run_id
    handoff_dir = run_dir / "handoffs" / handoff_id
    handoff_dir.mkdir(parents=True, exist_ok=True)

    state = {
        "schema_version": 2,
        "run_id": run_id,
        "handoff_id": handoff_id,
        "role": "planner",
        "depth": 0,
        "opened_at": "2026-01-01T00:00:00Z",
        **HANDOFF_STATE[status],
    }
    state_file = handoff_dir / "state.json"
    state_file.write_text(json.dumps(state), encoding="utf-8")
    # Give the file a fresh mtime so activity is a small non-negative number.
    os.utime(state_file, None)

    if state["status"] in ("open", "running"):
        active = run_dir / "active"
        active.mkdir(parents=True, exist_ok=True)
        (active / handoff_id).write_text("", encoding="utf-8")

    if runner_pid is not None:
        (run_dir / "runner.json").write_text(
            json.dumps({"run_id": run_id, "role": "planner", "pid": runner_pid}),
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
    cmd = ["python3", str(PROBE), "--runs-dir", str(runs_dir(tmp))]
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
            "probe.py must exist at .teamflow/skills/observer/scripts/probe.py",
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
                fields["fp"].startswith("h00001-planner:RUNNING:"),
                f"fp must start with the handoff id and status, got {fields.get('fp')!r}",
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
            out, rc = _run_probe(tmp, "r2", home, pi_running="0")
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

    def test_terminal_status_dead_pid_emits_exited(self):
        """Terminal status (PASS) + dead --pid → exited."""
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            home = tmp / "home"
            home.mkdir()
            _make_run_dir(tmp, "r5", "PASS")
            out, rc = _run_probe(tmp, "r5", home, pid=DEAD_PID)
            fields = parse_line(out.strip())
            self.assertEqual(fields["state"], "exited")
            self.assertEqual(rc, 1)

    def test_terminal_status_live_pid_emits_alive(self):
        """Terminal status (PASS) + live --pid → alive (--pid takes precedence)."""
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            home = tmp / "home"
            home.mkdir()
            _make_run_dir(tmp, "r6", "PASS")
            out, rc = _run_probe(tmp, "r6", home, pid=os.getpid())
            fields = parse_line(out.strip())
            self.assertEqual(fields["state"], "alive")
            self.assertEqual(rc, 0)


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
            os.utime(runs_dir(tmp) / "alpha", (old, old))
            os.utime(runs_dir(tmp) / "beta", None)
            out, rc = _run_probe(tmp, None, home, pi_running="1")
            fields = parse_line(out.strip())
            self.assertEqual(set(fields.keys()), ALLOWED_KEYS)
            self.assertEqual(fields["state"], "alive")
            # beta has phase 'p' status RUNNING; discovery should resolve it.
            self.assertTrue(
                fields["fp"].startswith("h00001-planner:RUNNING:"),
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
            os.utime(runs_dir(tmp) / "alpha", (old, old))
            os.utime(runs_dir(tmp) / "beta", None)
            out, rc = _run_probe(tmp, "alpha", home, pi_running="1")
            fields = parse_line(out.strip())
            self.assertEqual(fields["state"], "alive")
            self.assertTrue(fields["fp"].startswith("h00001-planner:RUNNING:"))
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

    def test_pass_plus_live_process_emits_alive(self):
        """Liveness comes from the process: PASS + live pi → alive."""
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            home = tmp / "home"
            home.mkdir()
            _make_run_dir(tmp, "rp3", "PASS")
            out, rc = _run_probe(tmp, "rp3", home, pi_running="1")
            fields = parse_line(out.strip())
            self.assertEqual(fields["state"], "alive")
            self.assertEqual(rc, 0)

    def test_fail_plus_live_process_emits_alive(self):
        """Liveness comes from the process: FAIL + live pi → alive."""
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            home = tmp / "home"
            home.mkdir()
            _make_run_dir(tmp, "rp4", "FAIL")
            out, rc = _run_probe(tmp, "rp4", home, pi_running="1")
            fields = parse_line(out.strip())
            self.assertEqual(fields["state"], "alive")
            self.assertEqual(rc, 0)

    def test_blocked_plus_live_process_emits_alive(self):
        """Liveness comes from the process: BLOCKED + live pi → alive."""
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            home = tmp / "home"
            home.mkdir()
            _make_run_dir(tmp, "rp5", "BLOCKED")
            out, rc = _run_probe(tmp, "rp5", home, pi_running="1")
            fields = parse_line(out.strip())
            self.assertEqual(fields["state"], "alive")
            self.assertEqual(rc, 0)

    def test_process_table_unreadable_emits_unknown(self):
        """When ps is unavailable, liveness is unknown."""
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            home = tmp / "home"
            home.mkdir()
            empty_bin = tmp / "empty-bin"
            empty_bin.mkdir()
            _make_run_dir(tmp, "rp6", "PASS")
            env = clean_env(home)
            # Do NOT set TEAMFLOW_PROBE_PI_RUNNING; break PATH so ps is absent.
            # Resolve python3 to an absolute path *before* overriding PATH so
            # the interpreter still runs while ps is not found.
            python_bin = shutil.which("python3")
            env["PATH"] = str(empty_bin)
            proc = subprocess.run(
                [python_bin, str(PROBE), "--runs-dir", str(runs_dir(tmp)),
                 "--run-id", "rp6"],
                cwd=str(ROOT),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=env,
                timeout=PROBE_TIMEOUT,
            )
            out = proc.stdout.decode("utf-8", "replace")
            fields = parse_line(out.strip())
            self.assertEqual(fields["state"], "unknown")
            self.assertEqual(proc.returncode, 2)


class ProbeRunnerAttributionTests(unittest.TestCase):
    """runner.json removes the guess: liveness is checked per run, not per host."""

    def test_recorded_runner_pid_is_used_without_pid_flag(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            home = tmp / "home"
            home.mkdir()
            _make_run_dir(tmp, "ra1", "RUNNING", runner_pid=os.getpid())
            # Force the process-table answer to the wrong value; the recorded
            # pid must win, or a concurrent run could be mistaken for this one.
            out, rc = _run_probe(tmp, "ra1", home, pi_running="0")
            fields = parse_line(out.strip())
            self.assertEqual(fields["state"], "alive")
            self.assertEqual(rc, 0)

    def test_dead_recorded_runner_pid_reports_exited(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            home = tmp / "home"
            home.mkdir()
            _make_run_dir(tmp, "ra2", "RUNNING", runner_pid=DEAD_PID)
            out, rc = _run_probe(tmp, "ra2", home, pi_running="1")
            fields = parse_line(out.strip())
            self.assertEqual(fields["state"], "exited")
            self.assertEqual(rc, 1)

    def test_explicit_pid_still_overrides_the_recorded_one(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            home = tmp / "home"
            home.mkdir()
            _make_run_dir(tmp, "ra3", "RUNNING", runner_pid=DEAD_PID)
            out, rc = _run_probe(tmp, "ra3", home, pid=os.getpid())
            self.assertEqual(parse_line(out.strip())["state"], "alive")
            self.assertEqual(rc, 0)


class ProbeHandoffFingerprintTests(unittest.TestCase):
    """The fingerprint follows the active handoff, not a single cursor."""

    def test_active_handoff_is_preferred_over_a_finished_one(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            home = tmp / "home"
            home.mkdir()
            _make_run_dir(tmp, "fp1", "PASS", handoff_id="h00001-planner")
            _make_run_dir(tmp, "fp1", "RUNNING", handoff_id="h00002-coder")
            out, _ = _run_probe(tmp, "fp1", home, pi_running="1")
            self.assertTrue(
                parse_line(out.strip())["fp"].startswith("h00002-coder:RUNNING:"),
                f"in-flight work must win: {out!r}",
            )

    def test_blocked_handoff_is_reported_in_the_fingerprint(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            home = tmp / "home"
            home.mkdir()
            _make_run_dir(tmp, "fp2", "BLOCKED")
            out, _ = _run_probe(tmp, "fp2", home, pi_running="1")
            self.assertIn(":BLOCKED:", parse_line(out.strip())["fp"])

    def test_run_without_handoffs_is_unknown(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            home = tmp / "home"
            home.mkdir()
            (runs_dir(tmp) / "empty-run").mkdir(parents=True)
            out, rc = _run_probe(tmp, "empty-run", home)
            self.assertEqual(parse_line(out.strip())["fp"], "-")
            self.assertEqual(rc, 2)

    def test_current_json_is_not_consulted(self):
        """The retired single cursor must not come back as a fallback."""
        source = PROBE.read_text(encoding="utf-8")
        self.assertNotIn("current.json", source)
        self.assertNotIn("phases", source)


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
            _, rc = _run_probe(tmp, "ec2", home, pi_running="0")
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
                 "--runs-dir", str(runs_dir(tmp)), "--run-id", "sub1"],
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
    """Probe output must never leak execute-loop data nouns."""

    def test_output_contains_no_execute_loop_nouns(self):
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
