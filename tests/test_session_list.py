"""Requirement tests for ``session list --format json`` (Part B).

These tests assert the *desired contract* for bounded, metadata-only session
discovery.  They must currently FAIL RED because the production code:

* globs ``*.jsonl`` at the sessions-parent level instead of inside the
  per-cwd subdirectory where files actually live;
* emits ``created_at``/``updated_at`` instead of ``created``/``updated``;
* has no default cap, no upper-bound rejection, and wrong header detection.
"""

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PI_RUNTIME = ROOT / ".teamflow" / "bin" / "pi-runtime"
TIMEOUT = 30

REQUIRED_KEYS = {"id", "model", "provider", "created", "updated", "message_count"}

FORBIDDEN_CONTENT_TOKENS = (
    "SECRET_PROMPT_CONTENT",
    "SECRET_RESPONSE_CONTENT",
    '"content"',
    '"role"',
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


def _encoded_cwd():
    """Compute the per-cwd subdir name pi-runtime will look for."""
    return "--" + str(ROOT).lstrip("/").replace("/", "-") + "--"


def _make_session_file(sessions_parent, records, mtime=None):
    """Write a JSONL session file inside the per-cwd subdir.

    Returns the file path.
    """
    subdir = sessions_parent / _encoded_cwd()
    subdir.mkdir(parents=True, exist_ok=True)
    path = subdir / f"sess_{os.urandom(4).hex()}.jsonl"
    lines = [json.dumps(r) for r in records]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    if mtime is not None:
        os.utime(path, (mtime, mtime))
    return path


def _run_session_list(sessions_parent, home, *extra_args):
    """Run ``pi-runtime session list --format json``; return (stdout, rc)."""
    env = clean_env(home)
    env["TEAMFLOW_PI_SESSION_DIR"] = str(sessions_parent)
    cmd = [
        "python3", str(PI_RUNTIME),
        "session", "list", "--format", "json",
        *extra_args,
    ]
    proc = subprocess.run(
        cmd,
        cwd=str(ROOT),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
        timeout=TIMEOUT,
    )
    return proc.stdout.decode("utf-8", "replace"), proc.returncode


# Canonical fixture records used across multiple tests.
_SESSION_RECORDS = [
    {
        "type": "session", "version": 3, "id": "test-sess-id",
        "timestamp": "2026-01-01T00:00:00.000Z", "cwd": "/test",
    },
    {
        "type": "model_change", "id": "mc1", "parentId": None,
        "timestamp": "2026-01-01T00:00:01.000Z",
        "provider": "zhipuai-coding-plan", "modelId": "glm-5.2",
    },
    {
        "type": "message", "id": "m1", "parentId": "mc1",
        "timestamp": "2026-01-01T00:00:02.000Z",
        "message": {
            "role": "user",
            "content": [{"type": "text", "text": "SECRET_PROMPT_CONTENT_DO_NOT_LEAK"}],
        },
    },
    {
        "type": "message", "id": "m2", "parentId": "m1",
        "timestamp": "2026-01-01T00:00:03.000Z",
        "message": {
            "role": "assistant",
            "content": [{"type": "text", "text": "SECRET_RESPONSE_CONTENT_DO_NOT_LEAK"}],
        },
    },
]


class SessionDiscoveryTests(unittest.TestCase):
    """S1: files inside the per-cwd subdir are discovered."""

    def test_finds_session_in_per_cwd_subdir(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            home = tmp / "home"
            home.mkdir()
            sessions = tmp / "sessions"
            sessions.mkdir()
            _make_session_file(sessions, _SESSION_RECORDS)
            out, rc = _run_session_list(sessions, home)
            self.assertEqual(rc, 0)
            data = json.loads(out)
            self.assertEqual(len(data), 1)


class SessionEmptyTests(unittest.TestCase):
    """S6: empty or missing directories return []."""

    def test_missing_sessions_dir_returns_empty(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            home = tmp / "home"
            home.mkdir()
            sessions = tmp / "does-not-exist"
            out, rc = _run_session_list(sessions, home)
            self.assertEqual(rc, 0)
            self.assertEqual(json.loads(out), [])

    def test_empty_sessions_dir_returns_empty(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            home = tmp / "home"
            home.mkdir()
            sessions = tmp / "sessions"
            sessions.mkdir()
            out, rc = _run_session_list(sessions, home)
            self.assertEqual(rc, 0)
            self.assertEqual(json.loads(out), [])

    def test_no_matching_per_cwd_subdir_returns_empty(self):
        """S6: sessions parent exists but no per-cwd subdir for ROOT."""
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            home = tmp / "home"
            home.mkdir()
            sessions = tmp / "sessions"
            sessions.mkdir()
            (sessions / "--some-other-cwd--").mkdir()
            out, rc = _run_session_list(sessions, home)
            self.assertEqual(rc, 0)
            self.assertEqual(json.loads(out), [])


class SessionKeySetTests(unittest.TestCase):
    """S3: each entry has exactly the required key set."""

    def test_entry_has_exact_key_set(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            home = tmp / "home"
            home.mkdir()
            sessions = tmp / "sessions"
            sessions.mkdir()
            _make_session_file(sessions, _SESSION_RECORDS)
            out, rc = _run_session_list(sessions, home)
            self.assertEqual(rc, 0)
            data = json.loads(out)
            self.assertEqual(len(data), 1)
            self.assertEqual(set(data[0].keys()), REQUIRED_KEYS)


class SessionNoContentTests(unittest.TestCase):
    """S4: no message body, prompt, reasoning, response, or tool payload."""

    def test_output_has_no_content(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            home = tmp / "home"
            home.mkdir()
            sessions = tmp / "sessions"
            sessions.mkdir()
            _make_session_file(sessions, _SESSION_RECORDS)
            out, rc = _run_session_list(sessions, home)
            self.assertEqual(rc, 0)
            for token in FORBIDDEN_CONTENT_TOKENS:
                with self.subTest(token=token):
                    self.assertNotIn(
                        token, out,
                        f"session list output must not contain {token!r}",
                    )


class SessionLimitTests(unittest.TestCase):
    """S2: default cap 10, --limit lowers, >10 rejected, 0 returns []."""

    def test_default_caps_at_ten(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            home = tmp / "home"
            home.mkdir()
            sessions = tmp / "sessions"
            sessions.mkdir()
            base = 1_000_000
            for i in range(12):
                _make_session_file(sessions, _SESSION_RECORDS, mtime=base + i)
            out, rc = _run_session_list(sessions, home)
            self.assertEqual(rc, 0)
            data = json.loads(out)
            self.assertEqual(len(data), 10)

    def test_limit_three(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            home = tmp / "home"
            home.mkdir()
            sessions = tmp / "sessions"
            sessions.mkdir()
            base = 1_000_000
            for i in range(12):
                _make_session_file(sessions, _SESSION_RECORDS, mtime=base + i)
            out, rc = _run_session_list(sessions, home, "--limit", "3")
            self.assertEqual(rc, 0)
            data = json.loads(out)
            self.assertEqual(len(data), 3)

    def test_limit_above_ten_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            home = tmp / "home"
            home.mkdir()
            sessions = tmp / "sessions"
            sessions.mkdir()
            base = 1_000_000
            for i in range(12):
                _make_session_file(sessions, _SESSION_RECORDS, mtime=base + i)
            out, rc = _run_session_list(sessions, home, "--limit", "11")
            self.assertNotEqual(rc, 0, "--limit 11 must be rejected")

    def test_limit_zero_returns_empty(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            home = tmp / "home"
            home.mkdir()
            sessions = tmp / "sessions"
            sessions.mkdir()
            _make_session_file(sessions, _SESSION_RECORDS)
            out, rc = _run_session_list(sessions, home, "--limit", "0")
            self.assertEqual(rc, 0)
            self.assertEqual(json.loads(out), [])

    def test_limit_negative_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            home = tmp / "home"
            home.mkdir()
            sessions = tmp / "sessions"
            sessions.mkdir()
            _make_session_file(sessions, _SESSION_RECORDS)
            out, rc = _run_session_list(sessions, home, "--limit", "-1")
            self.assertNotEqual(rc, 0, "negative --limit must be rejected")


class SessionNewestFirstTests(unittest.TestCase):
    """S2: entries sorted newest-first (mtime descending)."""

    def test_newest_first_order(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            home = tmp / "home"
            home.mkdir()
            sessions = tmp / "sessions"
            sessions.mkdir()
            for label, mtime in (("oldest", 1_000_000),
                                 ("middle", 2_000_000),
                                 ("newest", 3_000_000)):
                records = [dict(_SESSION_RECORDS[0], id=label), *_SESSION_RECORDS[1:]]
                _make_session_file(sessions, records, mtime=mtime)
            out, rc = _run_session_list(sessions, home, "--limit", "3")
            self.assertEqual(rc, 0)
            data = json.loads(out)
            ids = [e["id"] for e in data]
            self.assertEqual(ids, ["newest", "middle", "oldest"])


class SessionCorruptTests(unittest.TestCase):
    """S7: corrupt files are skipped, valid files still returned."""

    def test_truncated_header_skipped_valid_returned(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            home = tmp / "home"
            home.mkdir()
            sessions = tmp / "sessions"
            sessions.mkdir()
            _make_session_file(sessions, _SESSION_RECORDS, mtime=2_000_000)
            subdir = sessions / _encoded_cwd()
            bad = subdir / f"bad_{os.urandom(4).hex()}.jsonl"
            bad.write_text(
                '{"type":"session","id":"bad","timestamp":"2026',
                encoding="utf-8",
            )
            os.utime(bad, (1_000_000, 1_000_000))
            out, rc = _run_session_list(sessions, home)
            self.assertEqual(rc, 0)
            data = json.loads(out)
            self.assertEqual(len(data), 1)
            self.assertEqual(data[0]["id"], "test-sess-id")

    def test_garbage_file_skipped(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            home = tmp / "home"
            home.mkdir()
            sessions = tmp / "sessions"
            sessions.mkdir()
            _make_session_file(sessions, _SESSION_RECORDS, mtime=2_000_000)
            subdir = sessions / _encoded_cwd()
            bad = subdir / f"garbage_{os.urandom(4).hex()}.jsonl"
            bad.write_text(
                "this is not json at all\nneither is this\n",
                encoding="utf-8",
            )
            os.utime(bad, (1_000_000, 1_000_000))
            out, rc = _run_session_list(sessions, home)
            self.assertEqual(rc, 0)
            data = json.loads(out)
            self.assertEqual(len(data), 1)
            self.assertEqual(data[0]["id"], "test-sess-id")


class SessionMetadataTests(unittest.TestCase):
    """S5: metadata values correctly derived from records."""

    def test_metadata_values(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            home = tmp / "home"
            home.mkdir()
            sessions = tmp / "sessions"
            sessions.mkdir()
            _make_session_file(sessions, _SESSION_RECORDS)
            out, rc = _run_session_list(sessions, home)
            self.assertEqual(rc, 0)
            data = json.loads(out)
            self.assertEqual(len(data), 1)
            entry = data[0]
            self.assertEqual(entry["id"], "test-sess-id")
            self.assertEqual(entry["model"], "glm-5.2")
            self.assertEqual(entry["provider"], "zhipuai-coding-plan")
            self.assertEqual(entry["created"], "2026-01-01T00:00:00.000Z")
            self.assertEqual(entry["updated"], "2026-01-01T00:00:03.000Z")
            self.assertEqual(entry["message_count"], 2)


class SessionCostBoundTests(unittest.TestCase):
    """S8: only top *limit* files (by mtime) are returned — cost bounded."""

    def test_only_limit_files_returned(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            home = tmp / "home"
            home.mkdir()
            sessions = tmp / "sessions"
            sessions.mkdir()
            base = 1_000_000
            for i in range(15):
                _make_session_file(sessions, _SESSION_RECORDS, mtime=base + i)
            out, rc = _run_session_list(sessions, home, "--limit", "5")
            self.assertEqual(rc, 0)
            data = json.loads(out)
            self.assertEqual(len(data), 5)


if __name__ == "__main__":
    unittest.main()
