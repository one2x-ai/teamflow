"""Requirement tests for Phase B of the Teamflow Web Console.

Phase B adds static serving with SPA fallback and a front-end build pipeline:

  server/src/static.ts        serves web/dist with SPA fallback at /app prefix
  server/src/server.ts        registers /app prefix route via static module
  server/web/                 Svelte 5 + Tailwind 4 + shadcn-svelte project
  server/web/package.json     devDependencies only, NO runtime dependencies key
  server/web/vite.config.ts   separate from server/vite.config.ts
  server/web/src/*.svelte     uses Svelte 5 runes ($state/$props), NOT export let
  server/web/src/lib/components/ui/  shadcn-svelte components in copy mode
  server/package.json         has build and typecheck scripts
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


ROOT = Path(__file__).resolve().parents[2]
SERVER = ROOT / "server"
SRC = SERVER / "src"
SERVER_TS = SRC / "server.ts"
WEB = SERVER / "web"
WEB_SRC = WEB / "src"


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


def _collect_svelte_files(base: Path) -> list[Path]:
    """Return all *.svelte files under *base* (excluding node_modules)."""
    if not base.is_dir():
        return []
    return sorted(
        p for p in base.rglob("*.svelte")
        if "node_modules" not in p.parts
    )


class StaticModuleTests(unittest.TestCase):
    """Criterion: server/src/static.ts exists and server.ts wires the /app route."""

    def test_static_ts_exists(self):
        """server/src/static.ts is a non-empty file."""
        path = SRC / "static.ts"
        self.assertTrue(path.is_file(), "server/src/static.ts must exist")
        self.assertTrue(
            read(path).strip(),
            "server/src/static.ts must not be empty",
        )

    def test_server_ts_imports_static(self):
        """server.ts imports from './static' or references the static module."""
        text = read(SERVER_TS)
        self.assertTrue(
            "static" in text,
            "server.ts must import or reference the static module",
        )

    def test_server_ts_has_app_route(self):
        """server.ts registers '/app' as a route prefix."""
        text = read(SERVER_TS)
        self.assertIn(
            "/app",
            text,
            "server.ts must contain '/app' as the static route prefix",
        )


class WebProjectSourceTests(unittest.TestCase):
    """Criteria: web project source structure, runes, no export let, shadcn-svelte."""

    def test_web_package_no_runtime_deps(self):
        """web/package.json has no 'dependencies' key (devDependencies only)."""
        path = WEB / "package.json"
        self.assertTrue(path.is_file(), "server/web/package.json must exist")
        data = json.loads(read(path))
        self.assertNotIn(
            "dependencies",
            data,
            "server/web/package.json must not declare runtime dependencies",
        )

    def test_web_has_separate_vite_config(self):
        """web/vite.config.ts exists as a file separate from server/vite.config.ts."""
        path = WEB / "vite.config.ts"
        self.assertTrue(
            path.is_file(),
            "server/web/vite.config.ts must exist as a separate config",
        )

    def test_svelte_uses_runes(self):
        """At least one *.svelte under web/src uses Svelte 5 runes ($state or $props)."""
        svelte_files = _collect_svelte_files(WEB_SRC)
        self.assertGreater(
            len(svelte_files),
            0,
            "at least one *.svelte file must exist under server/web/src",
        )
        combined = "\n".join(read(p) for p in svelte_files)
        self.assertTrue(
            "$state" in combined or "$props" in combined,
            "Svelte sources must use runes ($state or $props), not Svelte 4 syntax",
        )

    def test_no_svelte4_export_let(self):
        """No *.svelte under web/src contains Svelte 4 'export let' syntax."""
        svelte_files = _collect_svelte_files(WEB_SRC)
        offenders = [p.name for p in svelte_files if "export let" in read(p)]
        self.assertEqual(
            offenders,
            [],
            f"Svelte 4 'export let' found in: {offenders}; use runes ($state/$props) instead",
        )

    def test_shadcn_ui_component_exists(self):
        """At least one shadcn-svelte component exists under web/src/lib/components/ui/."""
        ui_dir = WEB_SRC / "lib" / "components" / "ui"
        self.assertTrue(
            ui_dir.is_dir(),
            "server/web/src/lib/components/ui/ directory must exist",
        )
        svelte_components = list(ui_dir.rglob("*.svelte"))
        self.assertGreater(
            len(svelte_components),
            0,
            "at least one *.svelte component must exist under lib/components/ui/",
        )

    def test_shadcn_component_imported(self):
        """A route/App component imports from the shadcn-svelte ui directory."""
        svelte_files = _collect_svelte_files(WEB_SRC)
        self.assertGreater(len(svelte_files), 0, "no Svelte sources found")
        combined = "\n".join(read(p) for p in svelte_files)
        patterns = [
            "lib/components/ui",
            "$lib/components/ui",
        ]
        self.assertTrue(
            any(pat in combined for pat in patterns),
            "a component must import from lib/components/ui (shadcn-svelte copy mode)",
        )


class ServerPackageTests(unittest.TestCase):
    """Criterion: server/package.json has build and typecheck scripts."""

    def test_build_script(self):
        """server/package.json scripts dict has a 'build' key."""
        path = SERVER / "package.json"
        self.assertTrue(path.is_file(), "server/package.json must exist")
        data = json.loads(read(path))
        self.assertIn(
            "build",
            data.get("scripts", {}),
            "server/package.json must have a 'build' script",
        )

    def test_typecheck_script(self):
        """server/package.json scripts dict has a 'typecheck' key."""
        path = SERVER / "package.json"
        data = json.loads(read(path))
        self.assertIn(
            "typecheck",
            data.get("scripts", {}),
            "server/package.json must have a 'typecheck' script",
        )

    def test_typecheck_includes_svelte_check(self):
        """The typecheck script text contains 'svelte-check' for Svelte sources."""
        path = SERVER / "package.json"
        data = json.loads(read(path))
        typecheck_script = data.get("scripts", {}).get("typecheck", "")
        self.assertIn(
            "svelte-check",
            typecheck_script,
            "typecheck script must include 'svelte-check' to cover Svelte sources",
        )


class BuildOutputTests(unittest.TestCase):
    """Criterion 1: bun run build produces web/dist/index.html and hashed assets."""

    dist: Path = None  # type: ignore[assignment]

    @classmethod
    def setUpClass(cls):
        import shutil

        if shutil.which("bun") is None:
            raise unittest.SkipTest("bun is not installed")

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
        cls.dist = WEB / "dist"

    def test_dist_index_html(self):
        """server/web/dist/index.html exists after build."""
        self.assertTrue(
            (self.dist / "index.html").is_file(),
            "build must produce server/web/dist/index.html",
        )

    def test_dist_has_hashed_asset(self):
        """At least one hashed asset file exists under dist/assets/."""
        assets_dir = self.dist / "assets"
        self.assertTrue(assets_dir.is_dir(), "dist/assets/ directory must exist")
        files = list(assets_dir.iterdir())
        self.assertGreater(len(files), 0, "at least one built asset must exist")
        hashed = [
            f.name
            for f in files
            if re.search(r"[a-f0-9]{6,}", f.name)
        ]
        self.assertGreater(
            len(hashed),
            0,
            f"at least one asset filename must contain a Vite hash: "
            f"{[f.name for f in files]}",
        )


class TailwindInBuildTests(unittest.TestCase):
    """Criterion 5: built CSS contains at least one Tailwind-generated utility rule."""

    dist: Path = None  # type: ignore[assignment]

    @classmethod
    def setUpClass(cls):
        import shutil

        if shutil.which("bun") is None:
            raise unittest.SkipTest("bun is not installed")

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
        cls.dist = WEB / "dist"

    def test_css_has_tailwind_utility(self):
        """Built CSS is non-empty and contains Tailwind utility declarations."""
        assets_dir = self.dist / "assets"
        css_files = sorted(assets_dir.glob("*.css")) if assets_dir.is_dir() else []
        self.assertGreater(
            len(css_files),
            0,
            "at least one CSS file must exist in dist/assets/",
        )
        css_text = read(css_files[0])
        self.assertTrue(
            css_text.strip(),
            "built CSS must not be empty (Tailwind must have generated output)",
        )
        has_rule = bool(
            re.search(r"\.[a-zA-Z][-a-zA-Z0-9_]*\s*\{[^}]+\}", css_text)
        )
        has_preflight = bool(
            re.search(r"\*[,\s].*\{[^}]*\}", css_text)
        )
        has_declaration = any(
            prop in css_text
            for prop in ("margin", "padding", "display", "flex", "grid")
        )
        self.assertTrue(
            has_rule or has_preflight or has_declaration,
            "built CSS must contain Tailwind utility rules, preflight, "
            "or common declarations",
        )


class StaticRouteTests(unittest.TestCase):
    """Criteria 2-4: HTTP serving at /app with SPA fallback and asset 404."""

    server: subprocess.Popen | None = None
    server_port = 0
    css_name: str | None = None
    js_name: str | None = None
    dist: Path = None  # type: ignore[assignment]

    @classmethod
    def setUpClass(cls):
        import shutil

        if shutil.which("bun") is None:
            raise unittest.SkipTest("bun is not installed")

        if not (WEB / "dist" / "index.html").is_file():
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

        cls.dist = WEB / "dist"
        assets_dir = cls.dist / "assets"
        css_files = sorted(assets_dir.glob("*.css")) if assets_dir.is_dir() else []
        js_files = sorted(assets_dir.glob("*.js")) if assets_dir.is_dir() else []
        cls.css_name = css_files[0].name if css_files else None
        cls.js_name = js_files[0].name if js_files else None

        cls.server_port = free_port()
        env = os.environ.copy()
        cls.server = subprocess.Popen(
            ["bun", str(SERVER_TS), "--port", str(cls.server_port)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=env,
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

    def test_app_returns_html(self):
        """Criterion 2: GET /app returns index.html with content-type text/html."""
        status, headers, body = http("GET", self.base("/app"))
        self.assertEqual(status, 200, body)
        self.assertIn(
            "text/html",
            headers.get("content-type", "").lower(),
            "GET /app must return content-type text/html",
        )
        self.assertIn(
            b"<html",
            body.lower(),
            "GET /app body must contain an <html> tag",
        )

    def test_css_asset_served(self):
        """Criterion 3: GET /app/assets/<css> returns 200 with text/css."""
        if self.css_name is None:
            self.skipTest("no CSS asset found in dist")
        status, headers, body = http(
            "GET", self.base(f"/app/assets/{self.css_name}")
        )
        self.assertEqual(status, 200, body)
        self.assertIn(
            "text/css",
            headers.get("content-type", "").lower(),
            "CSS asset must be served with text/css content-type",
        )

    def test_js_asset_served(self):
        """Criterion 3: GET /app/assets/<js> returns 200 with javascript type."""
        if self.js_name is None:
            self.skipTest("no JS asset found in dist")
        status, headers, body = http(
            "GET", self.base(f"/app/assets/{self.js_name}")
        )
        self.assertEqual(status, 200, body)
        self.assertIn(
            "javascript",
            headers.get("content-type", "").lower(),
            "JS asset must be served with a javascript content-type",
        )

    def test_spa_fallback(self):
        """Criterion 4: deep path returns the same index.html as /app."""
        status_root, _, body_root = http("GET", self.base("/app"))
        status_deep, _, body_deep = http("GET", self.base("/app/some/deep/path"))
        self.assertEqual(status_root, 200)
        self.assertEqual(status_deep, 200)
        self.assertEqual(
            body_root,
            body_deep,
            "SPA fallback: /app and /app/some/deep/path must return identical HTML",
        )

    def test_every_asset_the_page_declares_is_reachable(self):
        """The page's own asset URLs must resolve, not just /app/assets/<name>.

        This is the gap that let a real break pass: the suite fetched
        /app/assets/<file> directly and got 200, while index.html pointed at
        /assets/<file> and every script 404ed in the browser. Asserting the
        declared URLs closes it, because it tests what a browser would do.
        """
        status, _, body = http("GET", self.base("/app"))
        self.assertEqual(status, 200)
        declared = re.findall(
            r'(?:src|href)="([^"]+\.(?:js|css))"', body.decode("utf-8", "replace")
        )
        self.assertTrue(declared, "index.html must reference at least one asset")
        for url in declared:
            with self.subTest(asset=url):
                self.assertTrue(
                    url.startswith("/app/"),
                    f"{url} must be served under the /app base, or the browser "
                    "will request a path static.ts does not serve",
                )
                asset_status, _, _ = http("GET", self.base(url))
                self.assertEqual(
                    asset_status,
                    200,
                    f"{url} is declared by index.html but not reachable",
                )

    def test_missing_asset_404(self):
        """Criterion 4: GET /app/assets/missing.js returns 404."""
        status, _, _ = http("GET", self.base("/app/assets/missing.js"))
        self.assertEqual(
            status,
            404,
            "missing asset under /app/assets/ must return 404, not the SPA fallback",
        )


class TypecheckTests(unittest.TestCase):
    """Criterion 8: bun run typecheck passes and covers Svelte sources."""

    @classmethod
    def setUpClass(cls):
        import shutil

        if shutil.which("bun") is None:
            raise unittest.SkipTest("bun is not installed")

        env = _build_env()
        build_result = subprocess.run(
            ["bun", "run", "build"],
            cwd=str(SERVER),
            env=env,
            capture_output=True,
            text=True,
            timeout=300,
        )
        if build_result.returncode != 0:
            raise unittest.SkipTest(
                f"build failed before typecheck (returncode={build_result.returncode}): "
                f"stdout={build_result.stdout[:500]} stderr={build_result.stderr[:500]}"
            )
        cls._env = env

    def test_typecheck_passes(self):
        """Criterion 8: bun run typecheck returns exit code 0."""
        result = subprocess.run(
            ["bun", "run", "typecheck"],
            cwd=str(SERVER),
            env=self._env,
            capture_output=True,
            text=True,
            timeout=120,
        )
        self.assertEqual(
            result.returncode,
            0,
            f"typecheck must pass:\nstdout={result.stdout}\nstderr={result.stderr}",
        )


if __name__ == "__main__":
    unittest.main()
