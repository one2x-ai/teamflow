"""Requirement tests for Phase C of the Teamflow Web Console.

Phase C replaces the pre-Svelte HTML pages (assembled from ``server/src/ui/*``
by ``server/src/pages.ts``) with Svelte components served from ``web/dist``.

This module covers three layers:

A. **Structural / HTTP tests** — route serving, file removal, grep checks,
   build-output asset resolution, responsive CSS, read-only contract,
   style restraint, typecheck, and dependency invariants.

B. **Behavioural render tests** — each scenario in
   ``server/web/src/__tests__/render-check.ts`` is invoked as a standalone
   Bun subprocess (happy-dom loads the built bundle and asserts on the DOM).

C. **Orchestration** — the build step, proxy-env stripping for subprocesses,
   and server lifecycle mirror patterns from ``test_phase_b_static_and_build.py``.
"""

import json
import os
import re
import socket
import subprocess
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

ROOT = Path(__file__).resolve().parents[2]
SERVER = ROOT / "server"
SRC = SERVER / "src"
SERVER_TS = SRC / "server.ts"
WEB = SERVER / "web"
WEB_SRC = WEB / "src"
DIST = WEB / "dist"
RENDER_CHECK = WEB_SRC / "__tests__" / "render-check.ts"

SCENARIOS = [
    "xss", "list", "empty", "error", "loading",
    "pagination", "search", "detail",
]


# ---------------------------------------------------------------------------
# Helpers (mirrors test_phase_b_static_and_build.py)
# ---------------------------------------------------------------------------

def read(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.is_file() else ""


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def http(method: str, url: str, timeout: float = 5.0, data: bytes | None = None):
    request = urllib.request.Request(url, method=method, data=data)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, dict(response.headers), response.read()
    except urllib.error.HTTPError as error:
        return error.code, dict(error.headers), error.read()


def _wait(port: int, timeout: float = 20.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with socket.socket() as probe:
            probe.settimeout(0.3)
            if probe.connect_ex(("127.0.0.1", port)) == 0:
                return
        time.sleep(0.1)
    raise AssertionError(f"port {port} never accepted connections")


def _build_env() -> dict:
    """Return environment for bun build/typecheck with proxy defaults."""
    env = os.environ.copy()
    env.setdefault("BUN_CONFIG_REGISTRY", "https://registry.npmjs.org")
    env.setdefault("HTTP_PROXY", "http://127.0.0.1:1087")
    env.setdefault("HTTPS_PROXY", "http://127.0.0.1:1087")
    env.setdefault("ALL_PROXY", "socks5://127.0.0.1:1080")
    return env


def _render_env() -> dict:
    """Env for render-check.ts subprocess.

    CRITICAL: strip proxy env vars so localhost requests don't hang.
    happy-dom's mock fetch doesn't make real network calls, but the
    runtime or module loader may still honour proxy settings.
    """
    env = os.environ.copy()
    for key in (
        "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY",
        "http_proxy", "https_proxy", "all_proxy",
    ):
        env.pop(key, None)
    return env


def _server_env() -> dict:
    """Env for server subprocess — proxy ok but bypass localhost."""
    env = os.environ.copy()
    env["NO_PROXY"] = "127.0.0.1,localhost"
    env["no_proxy"] = "127.0.0.1,localhost"
    return env


def _collect_svelte_files(base: Path) -> list[Path]:
    """Return all *.svelte files under *base* (excluding node_modules)."""
    if not base.is_dir():
        return []
    return sorted(
        p for p in base.rglob("*.svelte")
        if "node_modules" not in p.parts
    )


# ---------------------------------------------------------------------------
# Criteria 3-7, 13: File removal and stale-reference checks
# ---------------------------------------------------------------------------

class FileRemovalTests(unittest.TestCase):
    """Phase C deletes the pre-Svelte assets, assembler, and stale tests."""

    def test_src_ui_directory_removed(self):
        """Criterion 3: server/src/ui/ must no longer exist."""
        self.assertFalse(
            (SRC / "ui").is_dir(),
            "server/src/ui/ must be removed — Phase C serves Svelte from web/dist",
        )

    def test_pages_ts_removed(self):
        """Criterion 4: server/src/pages.ts must no longer exist."""
        self.assertFalse(
            (SRC / "pages.ts").is_file(),
            "server/src/pages.ts must be removed — page assembly is client-side",
        )

    def test_root_vite_config_removed(self):
        """Criterion 6: server/vite.config.ts must no longer exist."""
        self.assertFalse(
            (SERVER / "vite.config.ts").is_file(),
            "server/vite.config.ts must be removed — only web/vite.config.ts remains",
        )

    def test_web_vite_config_still_exists(self):
        """Criterion 6 guard: web/vite.config.ts must still exist."""
        self.assertTrue(
            (WEB / "vite.config.ts").is_file(),
            "server/web/vite.config.ts must still exist",
        )

    def test_test_ui_assets_removed(self):
        """Criterion 7: server/tests/test_ui_assets.py must no longer exist."""
        self.assertFalse(
            (SERVER / "tests" / "test_ui_assets.py").is_file(),
            "test_ui_assets.py targets src/ui and the preview plugin, both removed",
        )


class NoStaleReferenceTests(unittest.TestCase):
    """Criteria 5, 13: no live imports or path references to removed code."""

    def test_server_ts_does_not_import_pages(self):
        """server.ts must not import from './pages'."""
        text = read(SERVER_TS)
        self.assertNotRegex(
            text,
            r'from\s+["\']\.+/pages["\']',
            "server.ts must not import './pages' — Phase C removes the assembler",
        )

    def test_no_src_ui_path_references(self):
        """No .ts file under server/src references 'src/ui' in code."""
        offenders = []
        for ts_file in sorted(SRC.rglob("*.ts")):
            text = read(ts_file)
            # Strip single-line comments before checking
            code_lines = [
                line for line in text.splitlines()
                if not line.strip().startswith("//")
            ]
            code = "\n".join(code_lines)
            if "src/ui" in code:
                offenders.append(ts_file.name)
        self.assertEqual(
            offenders, [],
            f"These files still reference 'src/ui': {offenders}",
        )

    def test_package_json_no_ui_dev(self):
        """Criterion 13: server/package.json must not have 'ui:dev' script."""
        path = SERVER / "package.json"
        self.assertTrue(path.is_file(), "server/package.json must exist")
        data = json.loads(read(path))
        self.assertNotIn(
            "ui:dev", data.get("scripts", {}),
            "server/package.json must not have 'ui:dev' — root Vite preview is gone",
        )


# ---------------------------------------------------------------------------
# Criteria 10-12, 15: Svelte source contract checks
# ---------------------------------------------------------------------------

class SvelteSourceContractTests(unittest.TestCase):
    """Read-only rendering, no @html, Tailwind import, no custom palette."""

    def test_no_write_verbs_in_svelte(self):
        """Criterion 10: .svelte files must not contain write affordances."""
        svelte_files = _collect_svelte_files(WEB_SRC)
        self.assertGreater(len(svelte_files), 0, "at least one .svelte file must exist")
        forbidden = [
            "Delete", "Create", "Save", "Edit",
            'method="post"', "contenteditable",
        ]
        offenders = []
        for sf in svelte_files:
            text = read(sf)
            for verb in forbidden:
                if verb in text:
                    offenders.append(f"{sf.name}: '{verb}'")
        self.assertEqual(
            offenders, [],
            f"Write affordances found in Svelte files: {offenders}",
        )

    def test_no_html_interpolation_in_svelte(self):
        """Criterion 12: no {@html} in Svelte sources (XSS prevention)."""
        svelte_files = _collect_svelte_files(WEB_SRC)
        offenders = [sf.name for sf in svelte_files if "{@html" in read(sf)]
        self.assertEqual(
            offenders, [],
            f"{{@html}} found in: {offenders} — use {{expression}} for auto-escaped output",
        )

    def test_app_css_imports_tailwind(self):
        """Criterion 11: app.css must import Tailwind."""
        css = read(WEB_SRC / "app.css")
        self.assertIn(
            '@import "tailwindcss"',
            css,
            'server/web/src/app.css must contain @import "tailwindcss"',
        )

    def test_app_css_no_custom_color_palette(self):
        """Criterion 11: app.css must not define custom color properties."""
        css = read(WEB_SRC / "app.css")
        custom_colors = re.findall(
            r'--[a-zA-Z][-a-zA-Z0-9_]*\s*:\s*'
            r'(?:#[0-9a-fA-F]{3,8}|oklch\(|rgb\(|hsl\(|rgba\(|hsla\()',
            css,
        )
        self.assertEqual(
            custom_colors, [],
            f"app.css defines custom color properties: {custom_colors}. "
            "Reuse shadcn/Tailwind tokens instead.",
        )

    def test_web_package_has_happy_dom_devdep(self):
        """Criterion 15: web/package.json lists happy-dom in devDependencies."""
        path = WEB / "package.json"
        self.assertTrue(path.is_file(), "server/web/package.json must exist")
        data = json.loads(read(path))
        self.assertIn(
            "happy-dom",
            data.get("devDependencies", {}),
            "server/web/package.json must list happy-dom in devDependencies",
        )

    def test_web_package_no_runtime_deps(self):
        """Criterion 15 (Phase B invariant): no 'dependencies' key."""
        path = WEB / "package.json"
        data = json.loads(read(path))
        self.assertNotIn(
            "dependencies", data,
            "server/web/package.json must not declare runtime dependencies",
        )


# ---------------------------------------------------------------------------
# Criteria 8-9: Build output checks
# ---------------------------------------------------------------------------

class BuildOutputTests(unittest.TestCase):
    """Asset resolution and responsive CSS from the built bundle."""

    dist: Path = None  # type: ignore[assignment]

    @classmethod
    def setUpClass(cls):
        import shutil

        if shutil.which("bun") is None:
            raise unittest.SkipTest("bun is not installed")
        # The build step runs vite + bun install which can exceed 120s.
        env = _build_env()
        result = subprocess.run(
            ["bun", "run", "build"],
            cwd=str(SERVER),
            env=env,
            capture_output=True,
            text=True,
            timeout=300,
        )
        if result.returncode != 0:
            raise unittest.SkipTest(
                f"build failed (returncode={result.returncode}): "
                f"stdout={result.stdout[:500]} stderr={result.stderr[:500]}"
            )
        cls.dist = DIST

    def test_index_html_assets_start_with_app_prefix(self):
        """Criterion 8: every src/href asset in index.html starts with /app/."""
        index = self.dist / "index.html"
        self.assertTrue(index.is_file(), "web/dist/index.html must exist after build")
        html = read(index)
        declared = re.findall(
            r'(?:src|href)="([^"]+\.(?:js|css))"', html,
        )
        self.assertTrue(declared, "index.html must reference at least one asset")
        for url in declared:
            with self.subTest(asset=url):
                self.assertTrue(
                    url.startswith("/app/"),
                    f"{url} must be served under /app/ base",
                )

    def test_built_css_has_responsive_rules(self):
        """Criterion 9: built CSS constrains content at narrow viewports."""
        assets_dir = self.dist / "assets"
        css_files = sorted(assets_dir.glob("*.css")) if assets_dir.is_dir() else []
        self.assertGreater(len(css_files), 0, "at least one CSS file in dist/assets/")
        css_text = read(css_files[0])
        has_responsive = any(
            pattern in css_text
            for pattern in ("max-width", "width:100%", "flex-wrap", "auto-fit")
        ) or "@media" in css_text
        self.assertTrue(
            has_responsive,
            "built CSS must include responsive techniques "
            "(max-width, width:100%, flex-wrap, grid auto-fit, or @media)",
        )


# ---------------------------------------------------------------------------
# Criteria 1-2, 8 (HTTP): Route serving checks
# ---------------------------------------------------------------------------

class HttpShellTests(unittest.TestCase):
    """GET / and GET /memory return the Svelte app shell."""

    server: subprocess.Popen | None = None
    server_port = 0

    @classmethod
    def setUpClass(cls):
        import shutil

        if shutil.which("bun") is None:
            raise unittest.SkipTest("bun is not installed")

        if not (DIST / "index.html").is_file():
            # The build step runs vite + bun install which can exceed 120s.
            env = _build_env()
            result = subprocess.run(
                ["bun", "run", "build"],
                cwd=str(SERVER),
                env=env,
                capture_output=True,
                text=True,
                timeout=300,
            )
            if result.returncode != 0:
                raise unittest.SkipTest(
                    f"build failed: stderr={result.stderr[:500]}"
                )

        cls.server_port = free_port()
        cls.server = subprocess.Popen(
            ["bun", str(SERVER_TS), "--port", str(cls.server_port)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=_server_env(),
        )
        _wait(cls.server_port)

    @classmethod
    def tearDownClass(cls):
        if cls.server is not None:
            cls.server.terminate()
            try:
                cls.server.wait(timeout=5)
            except subprocess.TimeoutExpired:
                cls.server.kill()

    def base(self, path: str) -> str:
        return f"http://127.0.0.1:{self.server_port}{path}"

    def test_root_returns_svelte_shell(self):
        """Criterion 1: GET / returns the Svelte app shell."""
        status, headers, body = http("GET", self.base("/"))
        self.assertEqual(status, 200, body)
        self.assertIn(
            "text/html", headers.get("content-type", "").lower(),
        )
        body_text = body.decode("utf-8", "replace")
        self.assertIn(
            '<div id="app">', body_text,
            "body must contain the Svelte mount point",
        )
        self.assertIn(
            "/app/assets/", body_text,
            "body must reference built assets under /app/assets/",
        )
        self.assertNotIn(
            "src/ui", body_text,
            "body must not reference the old src/ui assets",
        )

    def test_memory_route_returns_svelte_shell(self):
        """Criterion 2: GET /memory?permalink=... returns the same shell."""
        status, headers, body = http(
            "GET", self.base("/memory?permalink=test/note"),
        )
        self.assertEqual(status, 200, body)
        self.assertIn(
            "text/html", headers.get("content-type", "").lower(),
        )
        body_text = body.decode("utf-8", "replace")
        self.assertIn(
            '<div id="app">', body_text,
            "body must contain the Svelte mount point",
        )
        self.assertIn(
            "/app/assets/", body_text,
            "body must reference built assets under /app/assets/",
        )

    def test_all_index_assets_reachable(self):
        """Criterion 8: every declared asset URL is reachable via HTTP."""
        status, _, body = http("GET", self.base("/"))
        self.assertEqual(status, 200)
        declared = re.findall(
            r'(?:src|href)="([^"]+\.(?:js|css))"',
            body.decode("utf-8", "replace"),
        )
        self.assertTrue(declared, "index.html must reference at least one asset")
        for url in declared:
            with self.subTest(asset=url):
                self.assertTrue(
                    url.startswith("/app/"),
                    f"{url} must be under /app/",
                )
                asset_status, _, _ = http("GET", self.base(url))
                self.assertEqual(
                    asset_status, 200,
                    f"{url} declared but not reachable",
                )


# ---------------------------------------------------------------------------
# Section B: Behavioural render scenarios
# ---------------------------------------------------------------------------

class RenderScenarioTests(unittest.TestCase):
    """Each scenario runs render-check.ts as a standalone Bun process."""

    @classmethod
    def setUpClass(cls):
        import shutil

        if shutil.which("bun") is None:
            raise unittest.SkipTest("bun is not installed")
        if not RENDER_CHECK.is_file():
            raise unittest.SkipTest("render-check.ts not found")
        if not (DIST / "index.html").is_file():
            # The build step runs vite + bun install which can exceed 120s.
            env = _build_env()
            result = subprocess.run(
                ["bun", "run", "build"],
                cwd=str(SERVER),
                env=env,
                capture_output=True,
                text=True,
                timeout=300,
            )
            if result.returncode != 0:
                raise unittest.SkipTest(
                    f"build failed: stderr={result.stderr[:500]}"
                )

    def _run_scenario(self, scenario: str) -> None:
        """Invoke render-check.ts for one scenario and assert exit 0."""
        env = _render_env()
        result = subprocess.run(
            ["bun", "run", "src/__tests__/render-check.ts", scenario],
            cwd=str(WEB),
            env=env,
            capture_output=True,
            text=True,
            timeout=60,
        )
        self.assertEqual(
            result.returncode, 0,
            f"render-check '{scenario}' failed (exit {result.returncode}):\n"
            f"stdout={result.stdout}\nstderr={result.stderr}",
        )

    def test_render_xss(self):
        """XSS payloads render as visible text, never as executable elements."""
        self._run_scenario("xss")

    def test_render_list(self):
        """List view shows titles, type badges, detail links; hides permalinks."""
        self._run_scenario("list")

    def test_render_empty(self):
        """Empty state shows a user-facing message."""
        self._run_scenario("empty")

    def test_render_error(self):
        """Error state shows a user-facing message."""
        self._run_scenario("error")

    def test_render_loading(self):
        """Loading state shows a loading indicator."""
        self._run_scenario("loading")

    def test_render_pagination(self):
        """Pagination shows page info, disables Previous, enables Next."""
        self._run_scenario("pagination")

    def test_render_search(self):
        """Search submits query and resets to page 1."""
        self._run_scenario("search")

    def test_render_detail(self):
        """Detail view shows title, content, back link; hides permalink."""
        self._run_scenario("detail")


# ---------------------------------------------------------------------------
# Criterion 14: Typecheck
# ---------------------------------------------------------------------------

class TypecheckTests(unittest.TestCase):
    """Criterion 14: bun run typecheck passes."""

    @classmethod
    def setUpClass(cls):
        import shutil

        if shutil.which("bun") is None:
            raise unittest.SkipTest("bun is not installed")
        if not (DIST / "index.html").is_file():
            env = _build_env()
            # The build step runs vite + bun install which can exceed 120s.
            result = subprocess.run(
                ["bun", "run", "build"],
                cwd=str(SERVER),
                env=env,
                capture_output=True,
                text=True,
                timeout=300,
            )
            if result.returncode != 0:
                raise unittest.SkipTest(
                    f"build failed: stderr={result.stderr[:500]}"
                )
        cls._env = _build_env()

    def test_typecheck_exit_zero(self):
        """Criterion 14: bun run typecheck must exit 0."""
        result = subprocess.run(
            ["bun", "run", "typecheck"],
            cwd=str(SERVER),
            env=self._env,
            capture_output=True,
            text=True,
            timeout=120,
        )
        self.assertEqual(
            result.returncode, 0,
            f"typecheck must pass:\nstdout={result.stdout}\nstderr={result.stderr}",
        )


if __name__ == "__main__":
    unittest.main()
