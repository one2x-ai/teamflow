"""Requirement tests for the read-only Teamflow memory server (`teamflow server`).

These tests describe the immutable contract for a Bun + TypeScript HTTP service
that browses the fully-local Basic Memory store. They are written BEFORE the
implementation and are expected to be RED until the server entrypoint, the
`teamflow server` dispatch branch, and the installer wiring all exist.

HTTP tests require the `bun` runtime on PATH and are skipped (not failed) when it
is absent. The installer-wiring test only inspects installed files and text, so
it never needs bun.
"""

import json
import os
import shutil
import socket
import subprocess
import tempfile
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SERVER_ENTRYPOINT = ROOT / "server" / "src" / "server.ts"
BUN = shutil.which("bun")
_NO_PROXY_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def write_executable(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    path.chmod(0o755)


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def make_recent_items(count: int) -> list[dict]:
    return [
        {
            "type": "note",
            "title": f"Note {index:02d}",
            "permalink": f"/notes/{index:02d}",
            "file_path": f"/memory/notes/{index:02d}.md",
            "created_at": "2025-01-01T00:00:00Z",
        }
        for index in range(count)
    ]


def write_fake_tools(bin_dir: Path) -> Path:
    """Place fake `pi` and `basic-memory` on an isolated PATH.

    The fake basic-memory logs every argv and serves canned JSON for the
    read-only tool subcommands the server must call. FAKE_BASIC_MEMORY_MODE
    (`fail` / `badjson` / `badshape`) exercises the 502 paths.
    """
    bin_dir.mkdir(parents=True, exist_ok=True)
    log = bin_dir.parent / "basic-memory.log"
    write_executable(
        bin_dir / "pi",
        """#!/bin/sh
if [ "${1:-}" = "--version" ]; then
  printf '0.82.1\\n'
elif [ "${1:-}" = "debug" ] && [ "${2:-}" = "skill" ]; then
  printf 'plan-change\\nbasic-memory-cli\\n'
fi
exit 0
""",
    )
    write_executable(
        bin_dir / "basic-memory",
        """#!/bin/sh
LOG="${FAKE_BASIC_MEMORY_LOG:-/dev/null}"
printf '%s\\n' "$*" >> "$LOG"
if [ "${1:-} ${2:-}" = "project info" ]; then
  exit 1
fi
if [ "${1:-}" = "status" ]; then
  printf '{}\\n'
  exit 0
fi
if [ "${1:-} ${2:-}" = "tool read-note" ]; then
  if [ "${FAKE_BASIC_MEMORY_MODE:-}" = "fail" ]; then
    exit 1
  fi
  if [ "${FAKE_BASIC_MEMORY_MODE:-}" = "badjson" ]; then
    printf 'not-valid-json{{{'
    exit 0
  fi
  if [ "${FAKE_BASIC_MEMORY_MODE:-}" = "badshape" ]; then
    printf '[]\\n'
    exit 0
  fi
  cat "${FAKE_BASIC_MEMORY_DETAIL_FILE:?}"
  exit 0
fi
if [ "${1:-} ${2:-}" = "tool recent-activity" ] || [ "${1:-} ${2:-}" = "tool search-notes" ]; then
  if [ "${FAKE_BASIC_MEMORY_MODE:-}" = "fail" ]; then
    exit 1
  fi
  if [ "${FAKE_BASIC_MEMORY_MODE:-}" = "badjson" ]; then
    printf 'not-valid-json{{{'
    exit 0
  fi
  PAGE_SIZE=""
  PENDING=""
  for arg in "$@"; do
    if [ -n "$PENDING" ]; then
      PAGE_SIZE="$arg"
      break
    fi
    if [ "$arg" = "--page-size" ]; then
      PENDING=1
    fi
  done
  if [ -n "$PAGE_SIZE" ] && [ "$PAGE_SIZE" -gt 100 ] 2>/dev/null; then
    printf 'Error: page_size must be <= 100, got %s\\n' "$PAGE_SIZE" >&2
    exit 1
  fi
  if [ "${1:-} ${2:-}" = "tool recent-activity" ]; then
    cat "${FAKE_BASIC_MEMORY_RECENT_FILE:?}"
  else
    cat "${FAKE_BASIC_MEMORY_SEARCH_FILE:?}"
  fi
  exit 0
fi
exit 0
""",
    )
    return log


def http_json(method: str, url: str, timeout: float = 5.0):
    """Return (status, parsed_body) for a request; parse JSON on 2xx and 4xx."""
    request = urllib.request.Request(url, method=method)
    try:
        with _NO_PROXY_OPENER.open(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8")
            return response.status, json.loads(raw) if raw else {}
    except urllib.error.HTTPError as error:
        raw = error.read().decode("utf-8")
        try:
            return error.code, json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            return error.code, raw


def port_refused(host: str, port: int, timeout: float = 1.0) -> bool:
    """True when nothing is listening (connection refused / unreachable)."""
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    probe.settimeout(timeout)
    try:
        probe.connect((host, port))
        return False
    except OSError:
        return True
    finally:
        probe.close()


class ServerFixture:
    """Isolated environment plus the real Bun server launched as a subprocess."""

    def __init__(self, testdir: Path):
        self.testdir = testdir
        self.bin = testdir / "fake-bin"
        self.log = write_fake_tools(self.bin)
        self.memory_home = testdir / "memory"
        self.memory_home.mkdir(parents=True, exist_ok=True)
        self.recent_file = testdir / "recent.json"
        self.search_file = testdir / "search.json"
        self.detail_file = testdir / "detail.json"
        self.server_log = testdir / "server.log"
        self.process: subprocess.Popen | None = None
        self.base_url = ""
        self._log_handle = None
        self.set_detail(
            {
                "title": "Memory detail",
                "permalink": "teamflow/projects/mcap/curated/memory-detail",
                "file_path": "projects/mcap/curated/Memory detail.md",
                "content": "# Memory detail\n\nReadable body.",
                "frontmatter": {"type": "teamflow_memory"},
            }
        )

    def set_recent(self, items: list[dict]) -> None:
        self.recent_file.write_text(json.dumps(items), encoding="utf-8")

    def set_search(self, results: list[dict], total: int) -> None:
        self.search_file.write_text(
            json.dumps({"results": results, "total": total}), encoding="utf-8"
        )

    def set_detail(self, detail: dict) -> None:
        self.detail_file.write_text(json.dumps(detail), encoding="utf-8")

    def base_env(self) -> dict[str, str]:
        env = os.environ.copy()
        for key in list(env):
            if key.startswith(("TEAMFLOW_", "WORKFLOW_", "OPENCODE_WORKFLOW_", "BASIC_MEMORY_")):
                env.pop(key)
        env.update(
            {
                "HOME": str(self.testdir / "home"),
                "PATH": f"{self.bin}:{env['PATH']}",
                "TEAMFLOW_MEMORY_HOME": str(self.memory_home),
                "FAKE_BASIC_MEMORY_LOG": str(self.log),
                "FAKE_BASIC_MEMORY_RECENT_FILE": str(self.recent_file),
                "FAKE_BASIC_MEMORY_SEARCH_FILE": str(self.search_file),
                "FAKE_BASIC_MEMORY_DETAIL_FILE": str(self.detail_file),
            }
        )
        return env

    def start(
        self,
        *,
        host: str = "127.0.0.1",
        port: int | None = None,
        env_overrides: dict[str, str] | None = None,
        flags: list[str] | None = None,
    ) -> str:
        env = self.base_env()
        if env_overrides:
            env.update(env_overrides)
        args = [BUN, str(SERVER_ENTRYPOINT)]
        if flags is None:
            flags = ["--host", host]
            if port is not None:
                flags += ["--port", str(port)]
        args.extend(flags)
        self._log_handle = self.server_log.open("wb")
        self.process = subprocess.Popen(
            args,
            cwd=str(ROOT),
            env=env,
            stdout=self._log_handle,
            stderr=subprocess.STDOUT,
        )
        self.base_url = f"http://{host}:{port}"
        return self.base_url

    def wait_ready(self, timeout: float = 8.0) -> None:
        deadline = time.monotonic() + timeout
        last_error: Exception | None = None
        while time.monotonic() < deadline:
            if self.process and self.process.poll() is not None:
                break
            try:
                with _NO_PROXY_OPENER.open(f"{self.base_url}/health", timeout=1) as response:
                    if response.status == 200:
                        return
            except Exception as error:  # retry until ready or timeout
                last_error = error
            time.sleep(0.2)
        log_tail = ""
        if self.server_log.is_file():
            log_tail = self.server_log.read_bytes()[-1200:].decode("utf-8", errors="replace")
        raise AssertionError(
            f"server did not become ready at {self.base_url} "
            f"(last_error={last_error!r}, log_tail={log_tail!r})"
        )

    def stop(self) -> None:
        if self.process and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=5)
        if self._log_handle is not None:
            self._log_handle.close()


@unittest.skipUnless(BUN, "bun runtime is required to launch the TypeScript server")
class MemoryServerHttpTests(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.fixture = ServerFixture(Path(self._tmp.name))
        self.fixture.set_recent(make_recent_items(5))
        self.fixture.set_search(
            [
                {"title": "Alpha result", "permalink": "/search/alpha"},
                {"title": "Beta result", "permalink": "/search/beta"},
                {"title": "Gamma result", "permalink": "/search/gamma"},
            ],
            total=999,
        )
        self.fixture.start(port=free_port())
        self.fixture.wait_ready()
        self.addCleanup(self.fixture.stop)

    # A1: health endpoints
    def test_health_endpoints_return_ok(self):
        for path in ("/health", "/api/health"):
            with self.subTest(path=path):
                status, body = http_json("GET", f"{self.fixture.base_url}{path}")
                self.assertEqual(status, 200)
                self.assertIsInstance(body, dict)
                self.assertEqual(body.get("status"), "ok")

    # A2: default schema + echoed pagination
    def test_default_memories_schema_and_echoed_pagination(self):
        status, body = http_json("GET", f"{self.fixture.base_url}/api/memories")
        self.assertEqual(status, 200)
        for key in ("items", "page", "page_size", "total", "total_pages", "query"):
            with self.subTest(key=key):
                self.assertIn(key, body)
        self.assertEqual(body["page"], 1)
        self.assertEqual(body["page_size"], 20)
        self.assertEqual(body["query"], "")
        self.assertEqual(body["total"], 5)
        self.assertEqual(body["total_pages"], 1)
        self.assertIsInstance(body["items"], list)
        self.assertEqual(len(body["items"]), 5)
        for item in body["items"]:
            self.assertIn("title", item)
            self.assertIn("permalink", item)

        status, body = http_json(
            "GET", f"{self.fixture.base_url}/api/memories?page=2&page_size=3"
        )
        self.assertEqual(status, 200)
        self.assertEqual(body["page"], 2)
        self.assertEqual(body["page_size"], 3)
        self.assertEqual(body["total"], 5)
        self.assertEqual(body["total_pages"], 2)  # ceil(5 / 3)
        self.assertEqual(
            [item["title"] for item in body["items"]], ["Note 03", "Note 04"]
        )  # candidates[3:6]

    # A3: no query -> recent-activity with exact flags; search-notes never called
    def test_no_query_invokes_recent_activity_with_exact_flags(self):
        http_json("GET", f"{self.fixture.base_url}/api/memories")
        calls = self.fixture.log.read_text(encoding="utf-8")
        self.assertIn(
            "tool recent-activity --timeframe 365d --page-size 100 --project teamflow --local",
            calls,
        )
        self.assertNotIn("tool search-notes", calls)

    # A3: query -> search-notes, echoed verbatim, total = candidate count
    def test_query_invokes_search_notes_and_echoes(self):
        status, body = http_json(
            "GET", f"{self.fixture.base_url}/api/memories?query=alpha%20beta"
        )
        self.assertEqual(status, 200)
        self.assertEqual(body["query"], "alpha beta")
        calls = self.fixture.log.read_text(encoding="utf-8")
        self.assertIn(
            "tool search-notes alpha beta --page-size 100 --project teamflow --local",
            calls,
        )
        self.assertNotIn("tool recent-activity", calls)
        # response.total is the candidate count (len(results)), not upstream total (999).
        self.assertEqual(body["total"], 3)
        self.assertEqual(body["total_pages"], 1)
        self.assertEqual(len(body["items"]), 3)
        self.assertEqual(
            [item["permalink"] for item in body["items"]],
            ["/search/alpha", "/search/beta", "/search/gamma"],
        )

    # A3: pagination slices candidates deterministically
    def test_pagination_slices_candidates(self):
        status, body = http_json(
            "GET", f"{self.fixture.base_url}/api/memories?page=2&page_size=2"
        )
        self.assertEqual(status, 200)
        # items = candidates[(page - 1) * page_size : page * page_size] = candidates[2:4]
        self.assertEqual(
            [item["title"] for item in body["items"]], ["Note 02", "Note 03"]
        )
        self.assertEqual(body["total"], 5)
        self.assertEqual(body["total_pages"], 3)  # max(1, ceil(5 / 2))

    # A4: invalid pagination -> 400 JSON (no 500, no crash)
    def test_invalid_pagination_returns_400(self):
        cases = [
            ("page", "0"), ("page", "-1"), ("page", "abc"), ("page", ""),
            ("page_size", "0"), ("page_size", "101"), ("page_size", "9999"),
            ("page_size", "foo"), ("page_size", ""),
        ]
        for key, value in cases:
            with self.subTest(key=key, value=value):
                status, body = http_json(
                    "GET", f"{self.fixture.base_url}/api/memories?{key}={value}"
                )
                self.assertEqual(status, 400, (key, value, status, body))
                self.assertIsInstance(body, dict)

    # A5: --host / --port flags honored
    def test_host_and_port_flags_are_honored(self):
        fixture = ServerFixture(Path(self._tmp.name) / "flags")
        fixture.set_recent(make_recent_items(1))
        fixture.set_search([], total=0)
        port = free_port()
        try:
            fixture.start(host="127.0.0.1", port=port)
            fixture.wait_ready()
            status, body = http_json("GET", f"{fixture.base_url}/health")
            self.assertEqual(status, 200)
            self.assertEqual(body.get("status"), "ok")
        finally:
            fixture.stop()

    # A5: TEAMFLOW_SERVER_HOST / TEAMFLOW_SERVER_PORT honored
    def test_host_and_port_env_are_honored(self):
        fixture = ServerFixture(Path(self._tmp.name) / "env")
        fixture.set_recent(make_recent_items(1))
        fixture.set_search([], total=0)
        port = free_port()
        try:
            fixture.start(
                host="127.0.0.1",
                port=port,
                flags=[],  # no CLI flags: env must drive the bind
                env_overrides={
                    "TEAMFLOW_SERVER_HOST": "127.0.0.1",
                    "TEAMFLOW_SERVER_PORT": str(port),
                },
            )
            fixture.wait_ready()
            status, body = http_json("GET", f"{fixture.base_url}/health")
            self.assertEqual(status, 200)
            self.assertEqual(body.get("status"), "ok")
        finally:
            fixture.stop()

    # A5: CLI port overrides env port
    def test_cli_port_overrides_env_port(self):
        fixture = ServerFixture(Path(self._tmp.name) / "prec-port")
        fixture.set_recent(make_recent_items(1))
        fixture.set_search([], total=0)
        flag_port = free_port()
        env_port = free_port()
        try:
            fixture.start(
                host="127.0.0.1",
                port=flag_port,
                env_overrides={"TEAMFLOW_SERVER_PORT": str(env_port)},
            )
            fixture.wait_ready()
            status, _ = http_json("GET", f"{fixture.base_url}/health")
            self.assertEqual(status, 200)  # flag_port lives => CLI won
            self.assertTrue(port_refused("127.0.0.1", env_port))  # env_port unused
        finally:
            fixture.stop()

    # A5: CLI host overrides env host (specific 127.0.0.1 bind, not 0.0.0.0)
    def test_cli_host_overrides_env_host(self):
        fixture = ServerFixture(Path(self._tmp.name) / "prec-host")
        fixture.set_recent(make_recent_items(1))
        fixture.set_search([], total=0)
        port = free_port()
        try:
            fixture.start(
                host="127.0.0.1",
                port=port,
                env_overrides={"TEAMFLOW_SERVER_HOST": "127.0.0.2"},
            )
            fixture.wait_ready()
            status, _ = http_json("GET", f"{fixture.base_url}/health")
            self.assertEqual(status, 200)  # 127.0.0.1 lives => CLI host won
            self.assertTrue(port_refused("127.0.0.2", port))  # not bound broadly
        finally:
            fixture.stop()

    # A6: read-only (write methods -> 405) and unknown path -> 404
    def test_write_methods_rejected_and_unknown_path_is_404(self):
        for method in ("POST", "PUT", "DELETE"):
            for path in ("/api/memories", "/health"):
                with self.subTest(method=method, path=path):
                    status, body = http_json(method, f"{self.fixture.base_url}{path}")
                    self.assertEqual(status, 405, (method, path, status, body))
                    self.assertIsInstance(body, dict)
        status, body = http_json("GET", f"{self.fixture.base_url}/no-such-path")
        self.assertEqual(status, 404)
        self.assertIsInstance(body, dict)

    # A8: upstream non-zero exit / unparseable JSON -> 502
    def test_upstream_failures_return_502(self):
        for mode in ("fail", "badjson"):
            with self.subTest(mode=mode):
                fixture = ServerFixture(Path(self._tmp.name) / f"upstream-{mode}")
                fixture.set_recent(make_recent_items(2))
                fixture.set_search([], total=0)
                port = free_port()
                try:
                    fixture.start(
                        host="127.0.0.1",
                        port=port,
                        env_overrides={"FAKE_BASIC_MEMORY_MODE": mode},
                    )
                    fixture.wait_ready()
                    status, body = http_json("GET", f"{fixture.base_url}/api/memories")
                    self.assertEqual(status, 502, (mode, status, body))
                    self.assertIsInstance(body, dict)
                finally:
                    fixture.stop()


class MemoryServerInstallerTests(unittest.TestCase):
    """Installer/dispatch wiring. File- and text-level only; no bun required."""

    def test_installer_wires_server_source_and_dispatch(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            home = root / "home"
            bin_dir = root / "fake-bin"
            launchers = root / "launchers"
            home.mkdir(parents=True)
            launchers.mkdir(parents=True)
            log = write_fake_tools(bin_dir)
            project = root / "target"
            project.mkdir()
            subprocess.run(["git", "init", "-q", str(project)], check=True)

            env = os.environ.copy()
            for key in list(env):
                if key.startswith(("TEAMFLOW_", "WORKFLOW_", "OPENCODE_WORKFLOW_", "BASIC_MEMORY_")):
                    env.pop(key)
            env.update(
                {
                    "HOME": str(home),
                    "PATH": f"{bin_dir}:{env['PATH']}",
                    "TEAMFLOW_HOME": str(home / ".teamflow"),
                    "TEAMFLOW_BIN_DIR": str(launchers),
                    "FAKE_BASIC_MEMORY_LOG": str(log),
                }
            )
            completed = subprocess.run(
                [str(ROOT / "scripts/init-project.sh"), str(project)],
                cwd=ROOT,
                env=env,
                text=True,
                capture_output=True,
                timeout=60,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)

            for relative in (
                ".teamflow/server/src/server.ts",
                ".teamflow/server/package.json",
                ".teamflow/server/tsconfig.json",
                ".teamflow/bin/server",
            ):
                with self.subTest(path=relative):
                    self.assertTrue((project / relative).is_file(), relative)

            manifest = json.loads(
                (project / ".teamflow/manifest.json").read_text(encoding="utf-8")
            )
            for manifest_key in manifest["files"]:
                with self.subTest(manifest_key=manifest_key):
                    self.assertTrue(manifest_key.startswith(".teamflow/"), manifest_key)

            teamflow_bin = (project / ".teamflow/bin/teamflow").read_text(encoding="utf-8")
            self.assertRegex(teamflow_bin, r'["\']server["\']')
            self.assertIn("bin/server", teamflow_bin)

            server_wrapper = (project / ".teamflow/bin/server").read_text(encoding="utf-8")
            self.assertIn("server.ts", server_wrapper)
            self.assertIn("bun", server_wrapper)


def http_get_raw(url, timeout=5.0):
    """GET (no proxy) returning (status, content_type, body).

    content_type is lowercased from the Content-Type header (default "");
    body is the decoded utf-8 payload. HTTPError is surfaced so 4xx/5xx
    responses can still be inspected.
    """
    request = urllib.request.Request(url, method="GET")
    try:
        with _NO_PROXY_OPENER.open(request, timeout=timeout) as response:
            body = response.read().decode("utf-8")
            content_type = response.headers.get("Content-Type", "")
            return response.status, content_type.lower(), body
    except urllib.error.HTTPError as error:
        body = error.read().decode("utf-8")
        content_type = error.headers.get("Content-Type", "")
        return error.code, content_type.lower(), body


@unittest.skipUnless(BUN, "bun runtime is required to launch the TypeScript server")
class MemoryServerUiTests(unittest.TestCase):
    """Read-only interactive HTML page contract (`GET /`).

    These tests assert on the served HTML text only (canonical markers, anchor
    ids, read-only/safe-rendering guarantees). They are written BEFORE the page
    exists and must be RED against the current 404 response.
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.fixture = ServerFixture(Path(self._tmp.name))
        self.fixture.set_recent(make_recent_items(5))
        self.fixture.set_search(
            [
                {"title": "Alpha result", "permalink": "/search/alpha"},
                {"title": "Beta result", "permalink": "/search/beta"},
                {"title": "Gamma result", "permalink": "/search/gamma"},
            ],
            total=999,
        )
        self.fixture.start(port=free_port())
        self.fixture.wait_ready()
        self.addCleanup(self.fixture.stop)

    # UI1: page + branding + responsive, complete (not a stub)
    def test_home_page_html_contract(self):
        status, content_type, body = http_get_raw(f"{self.fixture.base_url}/")
        with self.subTest(check="status_200"):
            self.assertEqual(status, 200, status)
        with self.subTest(check="content_type_text_html"):
            self.assertTrue(content_type.startswith("text/html"), content_type)
        with self.subTest(check="branding"):
            self.assertIn("Teamflow Memory", body)
        with self.subTest(check="responsive_viewport"):
            self.assertIn('<meta name="viewport"', body)
        with self.subTest(check="not_a_stub"):
            self.assertGreater(len(body), 1200, len(body))

    # UI2: on-load data fetch wiring
    def test_on_load_fetch_wiring(self):
        _, _, body = http_get_raw(f"{self.fixture.base_url}/")
        with self.subTest(marker="/api/memories"):
            self.assertIn("/api/memories", body)
        with self.subTest(marker="page_size=12"):
            self.assertIn("page_size=12", body)
        with self.subTest(marker="page=1"):
            self.assertIn("page=1", body)

    # UI3: search form + summary anchor
    def test_search_form_present(self):
        _, _, body = http_get_raw(f"{self.fixture.base_url}/")
        with self.subTest(marker="query_input"):
            self.assertIn('name="query"', body)
        with self.subTest(marker="form_open"):
            self.assertIn("<form", body)
        with self.subTest(marker="form_close"):
            self.assertIn("</form>", body)
        with self.subTest(marker="summary_anchor"):
            self.assertIn('id="summary"', body)

    # UI4: pagination controls
    def test_pagination_controls_present(self):
        _, _, body = http_get_raw(f"{self.fixture.base_url}/")
        with self.subTest(marker="prev_btn"):
            self.assertIn('id="prev-btn"', body)
        with self.subTest(marker="next_btn"):
            self.assertIn('id="next-btn"', body)
        with self.subTest(marker="page_info"):
            self.assertIn('id="page-info"', body)

    # UI5: loading/empty/error state anchors
    def test_state_anchors_present(self):
        _, _, body = http_get_raw(f"{self.fixture.base_url}/")
        for state in ("loading", "empty", "error"):
            with self.subTest(state=state):
                self.assertIn(f'data-state="{state}"', body)

    # UI6: cards region container
    def test_cards_region_present(self):
        _, _, body = http_get_raw(f"{self.fixture.base_url}/")
        with self.subTest(marker="cards_container"):
            self.assertIn('id="cards"', body)

    # UI7: read-only + safe rendering (DOM text, no innerHTML/write verbs)
    def test_read_only_and_safe_rendering(self):
        _, _, body = http_get_raw(f"{self.fixture.base_url}/")
        lowered = body.lower()
        with self.subTest(check="uses_textContent"):
            self.assertIn("textContent", body)
        with self.subTest(check="no_innerHTML"):
            self.assertNotIn("innerHTML", body)
        with self.subTest(check="no_method_post_attr"):
            self.assertNotIn('method="post"', lowered)
            self.assertNotIn("method='post'", lowered)
        with self.subTest(check="no_contenteditable"):
            self.assertNotIn("contenteditable", lowered)
        with self.subTest(check="no_fetch_write_verbs"):
            self.assertNotIn('method: "post"', lowered)
            self.assertNotIn("method: 'post'", lowered)
            self.assertNotIn('method: "put"', lowered)
            self.assertNotIn("method: 'put'", lowered)
            self.assertNotIn('method: "delete"', lowered)
            self.assertNotIn("method: 'delete'", lowered)
        with self.subTest(check="no_write_control_labels"):
            self.assertNotIn(">delete<", lowered)
            self.assertNotIn(">edit<", lowered)
            self.assertNotIn(">create<", lowered)

    # UI8: routing preserved alongside the new page
    def test_routing_preserved_alongside_page(self):
        with self.subTest(request="GET / is 200"):
            status, _, _ = http_get_raw(f"{self.fixture.base_url}/")
            self.assertEqual(status, 200, status)
        with self.subTest(request="POST / is 405"):
            status, _ = http_json("POST", f"{self.fixture.base_url}/")
            self.assertEqual(status, 405, status)
        with self.subTest(request="GET /no-such-path is 404"):
            status, body = http_json("GET", f"{self.fixture.base_url}/no-such-path")
            self.assertEqual(status, 404, status)
            self.assertIsInstance(body, dict)
        with self.subTest(request="GET /health still ok"):
            status, body = http_json("GET", f"{self.fixture.base_url}/health")
            self.assertEqual(status, 200, status)
            self.assertEqual(body.get("status"), "ok")

    # UI9: responsive form flex override
    def test_responsive_form_flex_override(self):
        """UI9: responsive form flex override in the <=560px media block.

        At <=560px the header switches to `flex-direction: column`, which makes
        the form's default `flex: 1 1 280px` reserve a ~280px *vertical* basis
        and stretch the search form tall. The media block must override the
        form's flex so it no longer reserves that 280px basis. This asserts on
        the served HTML text only (no headless browser / computed CSS).
        """
        _, _, body = http_get_raw(f"{self.fixture.base_url}/")
        marker = "@media (max-width: 560px)"
        with self.subTest(check="media_block_present"):
            self.assertIn(marker, body)
        # Extract the media-block body by walking brace depth from the marker.
        start = body.index(marker)
        open_brace = body.index("{", start)
        depth = 0
        end = None
        for index in range(open_brace, len(body)):
            char = body[index]
            if char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    end = index + 1
                    break
        self.assertIsNotNone(end, "unterminated @media block")
        media_block = body[open_brace:end]
        with self.subTest(check="form_rule_present"):
            self.assertIn("form", media_block)
        with self.subTest(check="form_flex_override_present"):
            self.assertIn("flex", media_block)
        with self.subTest(check="no_280px_vertical_basis"):
            self.assertNotIn("280px", media_block)


@unittest.skipUnless(BUN, "bun runtime is required to launch the TypeScript server")
class MemoryDetailPageTests(unittest.TestCase):
    """Clickable, read-only memory detail contract."""

    PERMALINK = "teamflow/projects/mcap/curated/memory-detail"

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.fixture = ServerFixture(Path(self._tmp.name))
        item = {"title": "Readable memory", "permalink": self.PERMALINK}
        self.fixture.set_recent([item])
        self.fixture.set_search([item], total=1)
        self.fixture.set_detail(
            {
                "title": "Readable memory",
                "permalink": self.PERMALINK,
                "file_path": "projects/mcap/curated/Readable memory.md",
                "content": (
                    "---\npermalink: teamflow/projects/mcap/curated/memory-detail\n---\n\n"
                    "# Readable memory\n\nFull detail body."
                ),
                "frontmatter": {"type": "teamflow_memory", "tags": ["mcap"]},
            }
        )
        self.fixture.start(port=free_port())
        self.fixture.wait_ready()
        self.addCleanup(self.fixture.stop)

    def test_list_uses_clickable_title_without_visible_raw_permalink(self):
        status, _, body = http_get_raw(f"{self.fixture.base_url}/")
        self.assertEqual(status, 200)
        self.assertIn("/memory?permalink=", body)
        self.assertIn("encodeURIComponent(permalink)", body)
        self.assertNotIn("link.textContent = permalink", body)

    def test_detail_page_is_read_only_safe_shell(self):
        status, content_type, body = http_get_raw(
            f"{self.fixture.base_url}/memory?permalink={urllib.parse.quote(self.PERMALINK)}"
        )
        self.assertEqual(status, 200)
        self.assertTrue(content_type.startswith("text/html"), content_type)
        self.assertIn("Back to memories", body)
        self.assertIn("/api/memory?permalink=", body)
        self.assertIn('id="detail-title"', body)
        self.assertIn('id="detail-content"', body)
        self.assertIn("textContent", body)
        self.assertNotIn("innerHTML", body)
        self.assertNotIn("file_path", body)

    def test_detail_api_reads_note_with_exact_flags(self):
        encoded = urllib.parse.quote(self.PERMALINK)
        status, body = http_json(
            "GET", f"{self.fixture.base_url}/api/memory?permalink={encoded}"
        )
        self.assertEqual(status, 200)
        self.assertEqual(body["title"], "Readable memory")
        self.assertEqual(body["content"], "# Readable memory\n\nFull detail body.")
        self.assertNotIn("teamflow/projects/mcap/", body["content"])
        calls = self.fixture.log.read_text(encoding="utf-8")
        self.assertIn(
            "tool read-note teamflow/projects/mcap/curated/memory-detail "
            "--include-frontmatter --project teamflow --local",
            calls,
        )

    def test_detail_api_missing_permalink_is_400(self):
        status, body = http_json("GET", f"{self.fixture.base_url}/api/memory")
        self.assertEqual(status, 400)
        self.assertIsInstance(body, dict)
        calls = self.fixture.log.read_text(encoding="utf-8") if self.fixture.log.exists() else ""
        self.assertNotIn("tool read-note", calls)

    def test_detail_api_upstream_failures_are_502(self):
        for mode in ("fail", "badjson", "badshape"):
            with self.subTest(mode=mode):
                fixture = ServerFixture(Path(self._tmp.name) / mode)
                fixture.set_recent([])
                fixture.set_search([], total=0)
                port = free_port()
                fixture.start(
                    port=port,
                    env_overrides={"FAKE_BASIC_MEMORY_MODE": mode},
                )
                try:
                    fixture.wait_ready()
                    encoded = urllib.parse.quote(self.PERMALINK)
                    status, body = http_json(
                        "GET", f"{fixture.base_url}/api/memory?permalink={encoded}"
                    )
                    self.assertEqual(status, 502, (mode, status, body))
                    self.assertIsInstance(body, dict)
                finally:
                    fixture.stop()


@unittest.skipUnless(BUN, "bun runtime is required to launch the TypeScript server")
class ScopedMemoryServerTests(unittest.TestCase):
    """Repository-scoped browsing via `teamflow server --dir <path>`.

    Current contract (criterion C): `--dir` NEVER passes `--permalink` to
    basic-memory. The server pages an UNSCOPED upstream call
    (recent-activity when there is no query, search-notes when there is one)
    and then filters the candidate list locally to the exact repository prefix
    `teamflow/projects/<slug>/`, preserving upstream order and dedupe. Other
    repositories and the global namespace never leak into a scoped response.
    """

    WORKSPACE_MARKER_ATTR = 'data-workspace="'

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.fixture = ServerFixture(Path(self._tmp.name))
        # Mix of in-scope (mcap), other-repository, and global permalinks so the
        # local prefix filter is observable in every response.
        self.fixture.set_recent(
            [
                {
                    "type": "note",
                    "title": "Recent Mcap 1",
                    "permalink": "teamflow/projects/mcap/curated/r1",
                    "file_path": "/memory/projects/mcap/curated/r1.md",
                    "created_at": "2025-01-01T00:00:00Z",
                },
                {
                    "type": "note",
                    "title": "Recent Other",
                    "permalink": "teamflow/projects/other/curated/r2",
                    "file_path": "/memory/projects/other/curated/r2.md",
                    "created_at": "2025-01-01T00:00:00Z",
                },
                {
                    "type": "note",
                    "title": "Recent Mcap 2",
                    "permalink": "teamflow/projects/mcap/curated/r3",
                    "file_path": "/memory/projects/mcap/curated/r3.md",
                    "created_at": "2025-01-01T00:00:00Z",
                },
            ]
        )
        self.fixture.set_search(
            [
                {"title": "Mcap A", "permalink": "teamflow/projects/mcap/curated/a"},
                {"title": "Other B", "permalink": "teamflow/projects/other/curated/b"},
                {"title": "Mcap C", "permalink": "teamflow/projects/mcap/curated/c"},
                {"title": "Global D", "permalink": "teamflow/global/curated/d"},
                {"title": "Mcap E", "permalink": "teamflow/projects/mcap/curated/e"},
            ],
            total=999,
        )
        self.scoped_dir = Path(self._tmp.name) / "mcap"
        self.scoped_dir.mkdir(parents=True, exist_ok=True)
        subprocess.run(["git", "init", "-q", str(self.scoped_dir)], check=True)
        port = free_port()
        self.fixture.start(
            host="127.0.0.1",
            port=port,
            flags=[
                "--host", "127.0.0.1",
                "--port", str(port),
                "--dir", str(self.scoped_dir),
            ],
        )
        self.fixture.wait_ready()
        self.addCleanup(self.fixture.stop)

    def _new_unscoped_fixture(self, name="unscoped"):
        fixture = ServerFixture(Path(self._tmp.name) / name)
        fixture.set_recent(make_recent_items(3))
        fixture.set_search([], total=0)
        port = free_port()
        fixture.start(host="127.0.0.1", port=port)
        fixture.wait_ready()
        self.addCleanup(fixture.stop)
        return fixture

    # 1. scoped no-query -> UNSCOPED recent-activity, never --permalink
    def test_scoped_no_query_uses_unscoped_recent_activity(self):
        status, body = http_json("GET", f"{self.fixture.base_url}/api/memories")
        self.assertEqual(status, 200)
        calls = self.fixture.log.read_text(encoding="utf-8")
        self.assertIn(
            "tool recent-activity --timeframe 365d --page-size 100 --project teamflow --local",
            calls,
        )
        self.assertNotIn("--permalink", calls)
        self.assertNotIn("tool search-notes", calls)
        # Local filter keeps only the mcap prefix, preserving upstream order.
        self.assertEqual(
            [item["permalink"] for item in body["items"]],
            ["teamflow/projects/mcap/curated/r1", "teamflow/projects/mcap/curated/r3"],
        )

    # 2. scoped query -> UNSCOPED search-notes, never --permalink
    def test_scoped_query_uses_unscoped_search_notes(self):
        status, body = http_json(
            "GET", f"{self.fixture.base_url}/api/memories?query=alpha%20beta"
        )
        self.assertEqual(status, 200)
        self.assertEqual(body["query"], "alpha beta")
        calls = self.fixture.log.read_text(encoding="utf-8")
        self.assertIn(
            "tool search-notes alpha beta --page-size 100 --project teamflow --local",
            calls,
        )
        self.assertNotIn("--permalink", calls)
        self.assertNotIn("tool recent-activity", calls)
        # Local filter keeps only mcap candidates in upstream order.
        self.assertEqual(
            [item["permalink"] for item in body["items"]],
            [
                "teamflow/projects/mcap/curated/a",
                "teamflow/projects/mcap/curated/c",
                "teamflow/projects/mcap/curated/e",
            ],
        )

    # 3. --permalink is never sent on any scoped request; no cross-project leak
    def test_scoped_never_passes_permalink_and_no_cross_project_leakage(self):
        http_json("GET", f"{self.fixture.base_url}/api/memories")
        http_json("GET", f"{self.fixture.base_url}/api/memories?query=foo")
        calls = self.fixture.log.read_text(encoding="utf-8")
        self.assertNotIn("--permalink", calls)
        status, body = http_json("GET", f"{self.fixture.base_url}/api/memories?query=foo")
        self.assertEqual(status, 200)
        for item in body["items"]:
            with self.subTest(permalink=item["permalink"]):
                self.assertTrue(
                    item["permalink"].startswith("teamflow/projects/mcap/"),
                    item["permalink"],
                )

    # 4. scoped GET / surfaces the slug via the workspace marker
    def test_scoped_workspace_label_visible(self):
        status, _, body = http_get_raw(f"{self.fixture.base_url}/")
        self.assertEqual(status, 200)
        self.assertIn('data-workspace="mcap"', body)

    # 5. without --dir the workspace marker must NOT appear
    def test_scoped_unscoped_marker_absent(self):
        fixture = self._new_unscoped_fixture()
        _, _, body = http_get_raw(f"{fixture.base_url}/")
        self.assertNotIn(self.WORKSPACE_MARKER_ATTR, body)

    # 6. scoped response preserves the full schema + deterministic slices
    def test_scoped_schema_and_pagination_preserved(self):
        status, body = http_json("GET", f"{self.fixture.base_url}/api/memories")
        self.assertEqual(status, 200)
        for key in ("items", "page", "page_size", "total", "total_pages", "query"):
            with self.subTest(key=key):
                self.assertIn(key, body)
        self.assertEqual(body["page"], 1)
        self.assertEqual(body["page_size"], 20)
        self.assertEqual(body["query"], "")
        # Only the 2 in-scope recent items survive the local filter.
        self.assertEqual(body["total"], 2)
        self.assertEqual(body["total_pages"], 1)
        self.assertEqual(len(body["items"]), 2)

        status, body = http_json(
            "GET", f"{self.fixture.base_url}/api/memories?page=2&page_size=2"
        )
        self.assertEqual(status, 200)
        # The no-query path remains recent-activity on every requested page.
        self.assertEqual(body["page"], 2)
        self.assertEqual(body["page_size"], 2)
        self.assertEqual(body["total"], 2)
        self.assertEqual(body["total_pages"], 1)
        self.assertEqual(body["items"], [])

    # 7. scoped mode preserves the 400/404/405/502 status contract
    def test_scoped_status_codes_preserved(self):
        for key, value in (("page", "0"), ("page_size", "101")):
            with self.subTest(bad=key, value=value):
                status, _ = http_json(
                    "GET", f"{self.fixture.base_url}/api/memories?{key}={value}"
                )
                self.assertEqual(status, 400, (key, value, status))
        with self.subTest(check="unknown_path_404"):
            status, _ = http_json("GET", f"{self.fixture.base_url}/no-such-path")
            self.assertEqual(status, 404)
        with self.subTest(check="post_405"):
            status, _ = http_json("POST", f"{self.fixture.base_url}/api/memories")
            self.assertEqual(status, 405)

        fail_dir = Path(self._tmp.name) / "mcap-fail"
        fail_dir.mkdir(parents=True, exist_ok=True)
        subprocess.run(["git", "init", "-q", str(fail_dir)], check=True)
        fail_fixture = ServerFixture(Path(self._tmp.name) / "scoped-fail")
        fail_fixture.set_recent(make_recent_items(1))
        fail_fixture.set_search([], total=0)
        fail_port = free_port()
        fail_fixture.start(
            host="127.0.0.1",
            port=fail_port,
            flags=[
                "--host", "127.0.0.1",
                "--port", str(fail_port),
                "--dir", str(fail_dir),
            ],
            env_overrides={"FAKE_BASIC_MEMORY_MODE": "fail"},
        )
        self.addCleanup(fail_fixture.stop)
        fail_fixture.wait_ready()
        with self.subTest(check="upstream_fail_502"):
            status, _ = http_json("GET", f"{fail_fixture.base_url}/api/memories")
            self.assertEqual(status, 502)

    # 8. without --dir, no-query still uses recent-activity and never permalink
    def test_unscoped_no_dir_still_recent_activity_no_permalink(self):
        fixture = self._new_unscoped_fixture()
        http_json("GET", f"{fixture.base_url}/api/memories")
        calls = fixture.log.read_text(encoding="utf-8")
        self.assertIn(
            "tool recent-activity --timeframe 365d --page-size 100 "
            "--project teamflow --local",
            calls,
        )
        self.assertNotIn("--permalink", calls)

    # 9. slug is derived from remote.origin.url (one2x-ai/mcap.git -> mcap);
    #    the permalink FLAG is still never passed upstream.
    def test_slug_from_remote_origin_url_without_permalink_flag(self):
        remote_dir = Path(self._tmp.name) / "remote-repo"
        remote_dir.mkdir(parents=True, exist_ok=True)
        subprocess.run(["git", "init", "-q", str(remote_dir)], check=True)
        subprocess.run(
            [
                "git", "-C", str(remote_dir), "remote", "add", "origin",
                "git@github.com:one2x-ai/mcap.git",
            ],
            check=True,
        )
        fixture = ServerFixture(Path(self._tmp.name) / "remote-fixture")
        fixture.set_recent(make_recent_items(1))
        fixture.set_search(
            [{"title": "Remote note", "permalink": "/remote/note"}], total=1
        )
        port = free_port()
        fixture.start(
            host="127.0.0.1",
            port=port,
            flags=[
                "--host", "127.0.0.1",
                "--port", str(port),
                "--dir", str(remote_dir),
            ],
        )
        self.addCleanup(fixture.stop)
        fixture.wait_ready()
        _, _, body = http_get_raw(f"{fixture.base_url}/")
        self.assertIn('data-workspace="mcap"', body)
        http_json("GET", f"{fixture.base_url}/api/memories")
        http_json("GET", f"{fixture.base_url}/api/memories?query=foo")
        calls = fixture.log.read_text(encoding="utf-8")
        self.assertNotIn("--permalink", calls)

    def test_scoped_detail_allows_selected_repository(self):
        permalink = "teamflow/projects/mcap/curated/memory-detail"
        encoded = urllib.parse.quote(permalink)
        status, body = http_json(
            "GET", f"{self.fixture.base_url}/api/memory?permalink={encoded}"
        )
        self.assertEqual(status, 200)
        self.assertEqual(body["permalink"], permalink)
        self.assertIn(
            "tool read-note teamflow/projects/mcap/curated/memory-detail",
            self.fixture.log.read_text(encoding="utf-8"),
        )

    def test_scoped_detail_rejects_other_repository_before_upstream(self):
        permalink = "teamflow/projects/teamflow/curated/private-note"
        encoded = urllib.parse.quote(permalink)
        status, body = http_json(
            "GET", f"{self.fixture.base_url}/api/memory?permalink={encoded}"
        )
        self.assertEqual(status, 403)
        self.assertIsInstance(body, dict)
        calls = self.fixture.log.read_text(encoding="utf-8") if self.fixture.log.exists() else ""
        self.assertNotIn(permalink, calls)
        self.assertNotIn("tool read-note", calls)

@unittest.skipUnless(BUN, "bun runtime is required to launch the TypeScript server")
class ScopedServerStartupTests(unittest.TestCase):
    """`teamflow server --dir <path>` startup validation.

    All checks must fail BEFORE any bind: nonzero exit + the chosen port stays
    refused. RED against the current server (no --dir validation).
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)

    def _start_and_expect_failure(self, extra_flags, *, timeout=3.0):
        fixture = ServerFixture(Path(self._tmp.name) / "startup")
        fixture.set_recent(make_recent_items(1))
        fixture.set_search([], total=0)
        host = "127.0.0.1"
        port = free_port()
        flags = ["--host", host, "--port", str(port), *extra_flags]
        fixture.start(host=host, port=port, flags=flags)
        self.addCleanup(fixture.stop)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if fixture.process and fixture.process.poll() is not None:
                break
            time.sleep(0.1)
        rc = fixture.process.poll() if fixture.process else None
        with self.subTest(check="exited_nonzero"):
            self.assertIsNotNone(rc, "server did not exit before timeout")
            self.assertNotEqual(rc, 0, f"server exited 0 but should fail (rc={rc})")
        with self.subTest(check="port_refused"):
            self.assertTrue(port_refused(host, port), "port was bound despite failure")
        return fixture

    # 9. trailing --dir (no following value) -> fail before bind
    def test_missing_dir_value_fails_startup(self):
        self._start_and_expect_failure(["--dir"])

    # 10. --dir <nonexistent path> -> fail before bind
    def test_nonexistent_dir_fails_startup(self):
        nonexistent = Path(self._tmp.name) / "does-not-exist"
        self._start_and_expect_failure(["--dir", str(nonexistent)])

    # 11. --dir <plain directory that is not a git repo> -> fail before bind
    def test_non_git_dir_fails_startup(self):
        plain = Path(self._tmp.name) / "plain-dir"
        plain.mkdir(parents=True, exist_ok=True)
        self._start_and_expect_failure(["--dir", str(plain)])


if __name__ == "__main__":
    unittest.main()
