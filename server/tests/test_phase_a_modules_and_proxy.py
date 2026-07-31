"""Requirement tests for Phase A of the Teamflow Web Console.

Design: docs/teamflow-web-console-design.md

Phase A splits the server into modules and adds the opencode reverse proxy:

  config.ts              CLI/env resolution
  http/{router,response} routing and response helpers
  memory/*               basic-memory CLI, scope filtering, routes
  opencode/*             upstream config, shared types, proxy
  server.ts              assembly only

The proxy contract, asserted here against a real upstream rather than a
mock: it forwards method, path, query, and body transparently; it streams
SSE without buffering and closes upstream when the client disconnects; it
never leaks the upstream Basic Auth credentials to the browser; and when
no upstream is configured it degrades to a structured 503 while memory
browsing keeps working.
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


class ModuleLayoutTests(unittest.TestCase):
    """Contract 1: the server is split by responsibility."""

    EXPECTED = (
        "config.ts",
        "http/router.ts",
        "http/response.ts",
        "memory/basic-memory.ts",
        "memory/scope.ts",
        "memory/routes.ts",
        "opencode/config.ts",
        "opencode/types.ts",
        "opencode/proxy.ts",
    )

    def test_every_module_exists(self):
        for relative in self.EXPECTED:
            with self.subTest(module=relative):
                path = SRC / relative
                self.assertTrue(path.is_file(), f"server/src/{relative} must exist")
                self.assertTrue(read(path).strip(), f"{relative} must not be empty")

    def test_entrypoint_only_assembles(self):
        """server.ts wires modules; it holds no business logic."""
        text = read(SERVER_TS)
        line_count = len(text.splitlines())
        self.assertLess(
            line_count,
            120,
            f"server.ts should be assembly-sized after the split, got {line_count}",
        )
        for marker in ("basic-memory", "recent-activity", "search-notes"):
            with self.subTest(marker=marker):
                self.assertNotIn(
                    marker,
                    text,
                    f"{marker} belongs in memory/, not the entrypoint",
                )

    def test_modules_are_typescript(self):
        """No plain JavaScript in backend modules.

        src/ui/*.js is front-end asset served to the browser and is replaced
        by the Svelte app in Phase C, so it is out of scope here.
        """
        offenders = [
            str(p.relative_to(SERVER))
            for p in SRC.rglob("*.js")
            if "node_modules" not in p.parts and "ui" not in p.parts
        ]
        self.assertEqual(offenders, [], f"backend must be TypeScript: {offenders}")

    def test_types_are_shared_not_duplicated(self):
        """Session/Message/Part types live in one place."""
        types_text = read(SRC / "opencode" / "types.ts")
        for name in ("Session", "Message", "Part"):
            with self.subTest(name=name):
                self.assertRegex(
                    types_text,
                    rf"(interface|type)\s+{name}\b",
                    f"opencode/types.ts must define {name}",
                )


class ProxyContractSourceTests(unittest.TestCase):
    """Contract 2-4 at the source level: forwarding, SSE, credentials."""

    def setUp(self):
        self.proxy = read(SRC / "opencode" / "proxy.ts")
        self.config = read(SRC / "opencode" / "config.ts")

    def test_proxy_forwards_request_method(self):
        self.assertRegex(
            self.proxy,
            r"method",
            "proxy must forward the request method",
        )

    def test_proxy_strips_its_own_prefix(self):
        """/api/oc/session must reach the upstream as /session."""
        self.assertRegex(
            self.proxy,
            r"/api/oc|PROXY_PREFIX|prefix",
            "proxy must define and strip its route prefix",
        )

    def test_proxy_attaches_basic_auth(self):
        self.assertRegex(
            self.proxy,
            r"[Aa]uthorization|[Bb]asic ",
            "proxy must attach Basic Auth to upstream requests",
        )

    def test_proxy_streams_response_body(self):
        """SSE requires the upstream body to pass through unbuffered."""
        self.assertRegex(
            self.proxy,
            r"\.body\b",
            "proxy must pass the upstream body through as a stream",
        )
        self.assertNotRegex(
            self.proxy,
            r"await\s+\w+\.text\(\)\s*;[\s\S]{0,200}new Response",
            "proxy must not buffer the whole body before responding",
        )

    def test_proxy_propagates_client_abort(self):
        self.assertRegex(
            self.proxy,
            r"signal",
            "proxy must forward the abort signal so upstream closes with the client",
        )

    def test_credentials_never_reach_the_response(self):
        """No code path copies credentials into a client-facing response."""
        self.assertNotRegex(
            self.proxy,
            r"(?i)(json|Response)\([^)]*password",
            "the proxy must never serialize credentials into a response",
        )

    def test_missing_upstream_degrades_to_503(self):
        """The degraded path is wired, and the status lives in one helper.

        The literal 503 belongs to http/response.ts; asserting it appears in
        proxy.ts would push the status code back into the proxy. What matters
        here is that the proxy routes an unconfigured upstream to that helper
        with a structured reason. UnconfiguredUpstreamTests verifies the
        actual 503 over HTTP.
        """
        self.assertIn(
            "unavailable",
            self.proxy,
            "the proxy must route an unconfigured upstream to the unavailable helper",
        )
        self.assertRegex(
            self.proxy,
            r"resolution\.configured|!\s*\w+\.configured",
            "the proxy must branch on whether the upstream is configured",
        )
        self.assertRegex(
            self.config,
            r"reason:\s*\"OPENCODE_[A-Z_]+\"",
            "each unconfigured case must carry a stable machine-readable reason",
        )
        response_helper = read(SRC / "http" / "response.ts")
        self.assertIn(
            "503",
            response_helper,
            "http/response.ts must define the 503 helper",
        )


class ProxyBehaviorTests(unittest.TestCase):
    """Contract 2-4 end to end against a real upstream."""

    upstream: subprocess.Popen | None = None
    server: subprocess.Popen | None = None
    upstream_port = 0
    server_port = 0

    @classmethod
    def setUpClass(cls):
        if not (SRC / "opencode" / "proxy.ts").is_file():
            raise unittest.SkipTest("proxy not implemented yet")
        import shutil

        if shutil.which("bun") is None:
            raise unittest.SkipTest("bun is not installed")

        cls.upstream_port = free_port()
        cls.server_port = free_port()

        # A minimal Basic-Auth upstream that echoes the request and can stream
        # SSE, so the proxy is tested against real HTTP rather than a mock.
        upstream_source = SERVER / "tests" / "fixtures" / "fake_opencode.ts"
        upstream_source.parent.mkdir(parents=True, exist_ok=True)
        upstream_source.write_text(
            """
const port = Number(process.argv[2]);
const expected = "Basic " + btoa("u:p");

Bun.serve({
  port,
  hostname: "127.0.0.1",
  async fetch(req) {
    const url = new URL(req.url);
    if (req.headers.get("authorization") !== expected) {
      return new Response(null, {
        status: 401,
        headers: { "www-authenticate": 'Basic realm="Secure Area"' },
      });
    }
    if (url.pathname === "/event") {
      const stream = new ReadableStream({
        async start(controller) {
          controller.enqueue(new TextEncoder().encode('data: {"n":1}\\n\\n'));
          await Bun.sleep(50);
          controller.enqueue(new TextEncoder().encode('data: {"n":2}\\n\\n'));
          controller.close();
        },
      });
      return new Response(stream, {
        headers: {
          "content-type": "text/event-stream",
          "cache-control": "no-cache",
          "x-accel-buffering": "no",
        },
      });
    }
    return Response.json({
      method: req.method,
      pathname: url.pathname,
      search: url.search,
      body: req.method === "POST" ? await req.text() : null,
    });
  },
});
console.error("fake upstream on " + port);
""",
            encoding="utf-8",
        )

        environment = os.environ.copy()
        environment.pop("TEAMFLOW_OPENCODE_URL", None)
        cls.upstream = subprocess.Popen(
            ["bun", str(upstream_source), str(cls.upstream_port)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=environment,
        )

        server_environment = environment.copy()
        server_environment.update(
            {
                "TEAMFLOW_OPENCODE_URL": f"http://127.0.0.1:{cls.upstream_port}",
                "TEAMFLOW_OPENCODE_USERNAME": "u",
                "TEAMFLOW_OPENCODE_PASSWORD": "p",
            }
        )
        cls.server = subprocess.Popen(
            ["bun", str(SERVER_TS), "--port", str(cls.server_port)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=server_environment,
        )
        cls._wait(cls.upstream_port)
        cls._wait(cls.server_port)

    @staticmethod
    def _wait(port: int, timeout: float = 20.0) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            with socket.socket() as probe:
                probe.settimeout(0.3)
                if probe.connect_ex(("127.0.0.1", port)) == 0:
                    return
            time.sleep(0.1)
        raise AssertionError(f"port {port} never accepted connections")

    @classmethod
    def tearDownClass(cls):
        for process in (cls.server, cls.upstream):
            if process is not None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
        fixture = SERVER / "tests" / "fixtures" / "fake_opencode.ts"
        fixture.unlink(missing_ok=True)

    def base(self, path: str) -> str:
        return f"http://127.0.0.1:{self.server_port}{path}"

    def test_forwards_path_with_prefix_stripped(self):
        status, _, body = http("GET", self.base("/api/oc/session"))
        self.assertEqual(status, 200, body)
        echoed = json.loads(body)
        self.assertEqual(echoed["pathname"], "/session")

    def test_forwards_query_string(self):
        status, _, body = http("GET", self.base("/api/oc/session?limit=5&q=x"))
        self.assertEqual(status, 200, body)
        echoed = json.loads(body)
        self.assertIn("limit=5", echoed["search"])
        self.assertIn("q=x", echoed["search"])

    def test_forwards_post_body_and_method(self):
        payload = json.dumps({"parts": [{"type": "text", "text": "hi"}]}).encode()
        status, _, body = http(
            "POST", self.base("/api/oc/session/ses_1/message"), data=payload
        )
        self.assertEqual(status, 200, body)
        echoed = json.loads(body)
        self.assertEqual(echoed["method"], "POST")
        self.assertEqual(echoed["pathname"], "/session/ses_1/message")
        self.assertEqual(json.loads(echoed["body"]), json.loads(payload))

    def test_upstream_credentials_are_not_exposed(self):
        """Neither the body nor any header may carry the credentials."""
        status, headers, body = http("GET", self.base("/api/oc/session"))
        self.assertEqual(status, 200)
        haystack = body.decode("utf-8", "replace") + json.dumps(dict(headers))
        for secret in ("Basic dTpw", "u:p", "TEAMFLOW_OPENCODE_PASSWORD"):
            with self.subTest(secret=secret):
                self.assertNotIn(secret, haystack)
        self.assertNotIn("authorization", {k.lower() for k in headers})

    def test_sse_is_streamed_with_buffering_disabled(self):
        request = urllib.request.Request(self.base("/api/oc/event"))
        with urllib.request.urlopen(request, timeout=10) as response:
            self.assertEqual(
                response.headers.get("content-type", "").split(";")[0],
                "text/event-stream",
            )
            self.assertEqual(response.headers.get("x-accel-buffering"), "no")
            first = response.readline()
            self.assertIn(b"data:", first)

    def test_memory_endpoints_still_respond(self):
        """Memory browsing does not depend on the proxy."""
        status, _, _ = http("GET", self.base("/api/health"))
        self.assertEqual(status, 200)


class UnconfiguredUpstreamTests(unittest.TestCase):
    """Contract 5: no upstream configured is a degraded mode, not a crash."""

    server: subprocess.Popen | None = None
    server_port = 0

    @classmethod
    def setUpClass(cls):
        if not (SRC / "opencode" / "config.ts").is_file():
            raise unittest.SkipTest("opencode config not implemented yet")
        import shutil

        if shutil.which("bun") is None:
            raise unittest.SkipTest("bun is not installed")

        cls.server_port = free_port()
        environment = os.environ.copy()
        for key in (
            "TEAMFLOW_OPENCODE_URL",
            "TEAMFLOW_OPENCODE_USERNAME",
            "TEAMFLOW_OPENCODE_PASSWORD",
        ):
            environment.pop(key, None)
        cls.server = subprocess.Popen(
            ["bun", str(SERVER_TS), "--port", str(cls.server_port)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=environment,
        )
        ProxyBehaviorTests._wait(cls.server_port)

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

    def test_server_starts_without_upstream(self):
        status, _, _ = http("GET", self.base("/api/health"))
        self.assertEqual(
            status, 200, "memory browsing must work with no opencode configured"
        )

    def test_proxy_returns_structured_503(self):
        status, _, body = http("GET", self.base("/api/oc/session"))
        self.assertEqual(status, 503)
        payload = json.loads(body)
        self.assertIn("reason", payload)
        self.assertTrue(
            str(payload["reason"]).strip(),
            "the 503 must name why the upstream is unavailable",
        )

    def test_degraded_response_carries_no_credentials(self):
        _, _, body = http("GET", self.base("/api/oc/session"))
        self.assertNotIn("password", body.decode("utf-8", "replace").lower())


if __name__ == "__main__":
    unittest.main()
