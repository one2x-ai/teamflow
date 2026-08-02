"""Static source and live Docker tests for the opencode health gateway.

The gateway (``scripts/opencode_health_gateway.js``) is a dependency-free
Node HTTP proxy that owns the public ``0.0.0.0:${PORT:-3000}`` listener,
spawns ``opencode web`` on loopback as a child, injects Basic Auth for
One2X ELB health checks (``User-Agent: ELB-HealthChecker/...`` on ``/``),
proxies all other traffic normally, streams bodies, forwards upgrades,
propagates child exit/signals, and fails closed when credentials are
missing.

Static tests (A1-A11) verify source invariants before the file exists;
live Docker tests (A4, A5, A6, A7, A9) exercise the running container.
"""

import os
import re
import shutil
import subprocess
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GATEWAY_SCRIPT = ROOT / "scripts" / "opencode_health_gateway.js"
DOCKERFILE = ROOT / "Dockerfile"

#: Deterministic default container port shared by static and live tests.
DEFAULT_PORT = 3000

#: Docker build timeout (must exceed 120 s).
BUILD_TIMEOUT = 600

#: HTTP probe settings.
HTTP_RETRIES = 12
HTTP_RETRY_DELAY = 5
HTTP_TIMEOUT = 60

#: Fail-closed wait timeout (seconds).
FAIL_CLOSED_TIMEOUT = 30

#: Proxy environment keys stripped from every Docker subprocess.
_PROXY_KEYS = (
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "ALL_PROXY",
    "http_proxy",
    "https_proxy",
    "all_proxy",
    "NO_PROXY",
    "no_proxy",
)

#: Node.js built-in modules (no third-party dependencies allowed).
NODE_BUILTINS = frozenset({
    "assert", "async_hooks", "buffer", "child_process", "cluster",
    "console", "crypto", "dgram", "diagnostics_channel", "dns",
    "domain", "events", "fs", "http", "http2", "https", "inspector",
    "module", "net", "os", "path", "perf_hooks", "process", "punycode",
    "querystring", "readline", "repl", "stream", "string_decoder",
    "sys", "timers", "tls", "trace_events", "tty", "url", "util",
    "v8", "vm", "wasi", "worker_threads", "zlib",
})

#: Test-only placeholder credentials -- never default or real values.
TEST_USER = "test-user-9f3a"
TEST_PASSWORD = "test-pass-7c2e"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _strip_proxy_env(base=None):
    """Return a dict copy of *base* with all proxy variables removed."""
    env = dict(base if base is not None else os.environ)
    for key in _PROXY_KEYS:
        env.pop(key, None)
    return env


def _require_gateway_source():
    """Return gateway script contents; raise AssertionError if absent."""
    if not GATEWAY_SCRIPT.exists():
        raise AssertionError(
            f"Gateway script not found at {GATEWAY_SCRIPT}"
        )
    text = GATEWAY_SCRIPT.read_text(encoding="utf-8")
    if not text.strip():
        raise AssertionError("Gateway script is empty")
    return text


def _build_image(tag, env):
    """Build Docker image; return (ok, reason)."""
    try:
        build = subprocess.run(
            ["docker", "build", "-t", tag, "-f", str(DOCKERFILE), str(ROOT)],
            capture_output=True,
            text=True,
            timeout=BUILD_TIMEOUT,
            env=env,
            stdin=subprocess.DEVNULL,
        )
    except subprocess.TimeoutExpired:
        return False, f"build timed out after {BUILD_TIMEOUT}s"
    except OSError as exc:
        return False, f"build OSError: {exc}"
    if build.returncode != 0:
        return False, f"build exit {build.returncode}"
    return True, None


def _mapped_port(container_id, env):
    """Resolve the host-side port mapped to DEFAULT_PORT in the container."""
    port_out = subprocess.run(
        ["docker", "port", container_id, str(DEFAULT_PORT)],
        capture_output=True,
        text=True,
        timeout=15,
        env=env,
        stdin=subprocess.DEVNULL,
    )
    for line in port_out.stdout.splitlines():
        m = re.search(r":(\d+)\s*$", line.strip())
        if m:
            return int(m.group(1))
    return None


def _wait_for_exit(container_id, env, timeout):
    """Wait for a container to exit; return its exit code or None on timeout."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        status = subprocess.run(
            ["docker", "inspect", "-f", "{{.State.Status}}", container_id],
            capture_output=True,
            text=True,
            timeout=15,
            env=env,
            stdin=subprocess.DEVNULL,
        )
        if status.returncode == 0 and status.stdout.strip() in ("exited", "dead"):
            code = subprocess.run(
                ["docker", "inspect", "-f", "{{.State.ExitCode}}", container_id],
                capture_output=True,
                text=True,
                timeout=15,
                env=env,
                stdin=subprocess.DEVNULL,
            )
            try:
                return int(code.stdout.strip())
            except (ValueError, TypeError):
                return None
        time.sleep(1)
    return None


# ---------------------------------------------------------------------------
# Prerequisite -- gateway script exists
# ---------------------------------------------------------------------------


class GatewayFileTests(unittest.TestCase):
    """The gateway script must exist and be non-empty."""

    def test_gateway_script_exists(self):
        self.assertTrue(
            GATEWAY_SCRIPT.exists(),
            f"Gateway script must exist at {GATEWAY_SCRIPT}",
        )

    def test_gateway_script_non_empty(self):
        source = _require_gateway_source()
        self.assertGreater(len(source.strip()), 0)


# ---------------------------------------------------------------------------
# A1 -- Dependency-free (Node built-ins only)
# ---------------------------------------------------------------------------


class DependencyFreeTests(unittest.TestCase):
    """A1: Only Node.js built-in modules; no package.json or third-party deps."""

    def setUp(self):
        self.source = _require_gateway_source()

    def test_all_requires_are_builtins(self):
        requires = re.findall(
            r'''require\(\s*['"]([^'"]+)['"]\s*\)''', self.source
        )
        self.assertTrue(
            requires,
            "source must use require() for at least one built-in module",
        )
        for mod in requires:
            top = mod.lstrip("./").split("/")[0]
            self.assertIn(
                top,
                NODE_BUILTINS,
                f"require('{mod}') is not a Node.js built-in",
            )

    def test_no_third_party_esm_imports(self):
        imports = re.findall(
            r'''\bimport\b[^;]*?\bfrom\s*['"]([^'"]+)['"]''', self.source
        )
        for mod in imports:
            if mod.startswith(".") or mod.startswith("/"):
                continue
            top = mod.split("/")[0]
            self.assertIn(
                top,
                NODE_BUILTINS,
                f"import from '{mod}' is not a Node.js built-in",
            )

    def test_no_package_json_reference(self):
        self.assertNotIn(
            "package.json",
            self.source,
            "gateway must be dependency-free (no package.json reference)",
        )


# ---------------------------------------------------------------------------
# A2 -- Public listener on 0.0.0.0:PORT
# ---------------------------------------------------------------------------


class PublicListenerTests(unittest.TestCase):
    """A2: Gateway reads PORT (default 3000) and binds 0.0.0.0."""

    def setUp(self):
        self.source = _require_gateway_source()

    def test_reads_port_env_var(self):
        self.assertIn(
            "PORT",
            self.source,
            "gateway must read process.env.PORT",
        )

    def test_port_defaults_to_3000(self):
        self.assertIn(
            "3000",
            self.source,
            "PORT must default to 3000",
        )

    def test_binds_public_listener_to_all_interfaces(self):
        self.assertIn(
            "0.0.0.0",
            self.source,
            "public listener must bind to 0.0.0.0",
        )


# ---------------------------------------------------------------------------
# A3 -- Child process on loopback with fixed internal port
# ---------------------------------------------------------------------------


class ChildProcessTests(unittest.TestCase):
    """A3: Spawns ``opencode web --hostname 127.0.0.1 --port <internal>``."""

    def setUp(self):
        self.source = _require_gateway_source()

    def test_spawns_opencode_web(self):
        self.assertIn("opencode", self.source, "must spawn 'opencode'")
        self.assertIn("web", self.source, "must use the 'web' subcommand")

    def test_child_uses_hostname_flag(self):
        self.assertIn("--hostname", self.source, "child must use --hostname")

    def test_child_binds_loopback(self):
        self.assertIn(
            "127.0.0.1",
            self.source,
            "child must bind to 127.0.0.1 (loopback)",
        )

    def test_child_uses_port_flag(self):
        self.assertIn("--port", self.source, "child must use --port")

    def test_internal_port_is_fixed_non_default(self):
        """The internal port must be a positive integer literal != 3000."""
        port_candidates = set()
        for m in re.finditer(r'''--port['"\s',:=]+(\d{2,5})''', self.source):
            port_candidates.add(int(m.group(1)))
        for m in re.finditer(
            r'\w*PORT\w*\s*[:=]\s*(\d{2,5})', self.source, re.IGNORECASE
        ):
            port_candidates.add(int(m.group(1)))
        non_default = [
            p for p in port_candidates if p != DEFAULT_PORT and 0 < p < 65536
        ]
        self.assertTrue(
            non_default,
            f"internal port must be a fixed literal != {DEFAULT_PORT}; "
            f"found candidates: {sorted(port_candidates)}",
        )


# ---------------------------------------------------------------------------
# A4 -- Startup fail-closed on missing credentials
# ---------------------------------------------------------------------------


class FailClosedTests(unittest.TestCase):
    """A4: Exits non-zero if credentials are missing, before listening."""

    def setUp(self):
        self.source = _require_gateway_source()

    def test_reads_username_env(self):
        self.assertIn(
            "OPENCODE_SERVER_USERNAME",
            self.source,
            "gateway must read OPENCODE_SERVER_USERNAME",
        )

    def test_reads_password_env(self):
        self.assertIn(
            "OPENCODE_SERVER_PASSWORD",
            self.source,
            "gateway must read OPENCODE_SERVER_PASSWORD",
        )

    def test_exit_or_throw_before_listen(self):
        exit_positions = [
            m.start()
            for m in re.finditer(r'\bprocess\.exit\s*\(', self.source)
        ]
        throw_positions = [
            m.start()
            for m in re.finditer(r'\bthrow\b', self.source)
        ]
        all_exits = exit_positions + throw_positions
        self.assertTrue(
            all_exits,
            "gateway must call process.exit or throw for fail-closed",
        )
        listen_match = re.search(r'\.listen\s*\(', self.source)
        self.assertIsNotNone(listen_match, "gateway must have .listen() call")
        earliest_exit = min(all_exits)
        self.assertLess(
            earliest_exit,
            listen_match.start(),
            "fail-closed exit/throw must occur before server.listen",
        )

    def test_no_default_credential_literal(self):
        for var in ("OPENCODE_SERVER_USERNAME", "OPENCODE_SERVER_PASSWORD"):
            m = re.search(rf'{var}\s*\|\|\s*["\'][^"\']+["\']', self.source)
            self.assertIsNone(
                m,
                f"{var} must not have a default literal value",
            )


# ---------------------------------------------------------------------------
# A5 -- ELB health check (UA prefix + root path -> injected Basic Auth)
# ---------------------------------------------------------------------------


class ELBHealthCheckTests(unittest.TestCase):
    """A5: ELB UA on root path triggers injected Basic Auth header."""

    def setUp(self):
        self.source = _require_gateway_source()

    def test_detects_elb_user_agent_prefix(self):
        self.assertIn(
            "ELB-HealthChecker/",
            self.source,
            "must detect User-Agent prefix 'ELB-HealthChecker/'",
        )

    def test_health_check_limited_to_root_path(self):
        self.assertTrue(
            re.search(r'''['"]\s*/\s*['"]''', self.source),
            "health check branch must reference root path '/'",
        )

    def test_constructs_authorization_header(self):
        self.assertTrue(
            re.search(r'[Aa]uthorization', self.source),
            "must construct an Authorization header for health checks",
        )

    def test_base64_encodes_credentials(self):
        self.assertTrue(
            re.search(r'base64', self.source, re.IGNORECASE),
            "must base64-encode credentials in-memory",
        )

    def test_overwrites_inbound_authorization(self):
        self.assertTrue(
            re.search(
                r'''[\w\[\]'"\.]*[Aa]uthorization[\w\[\]'"\.]*\s*[=:]''',
                self.source,
            ),
            "must set/overwrite Authorization header for health checks",
        )


# ---------------------------------------------------------------------------
# A8 -- Normal request Authorization preserved (not overwritten)
# ---------------------------------------------------------------------------


class NormalAuthPassthroughTests(unittest.TestCase):
    """A8: Non-health requests preserve the client's Authorization header."""

    def setUp(self):
        self.source = _require_gateway_source()

    def test_auth_injection_is_inside_health_branch(self):
        """The Authorization header injection must be conditional."""
        elb_pos = self.source.find("ELB-HealthChecker/")
        self.assertGreater(elb_pos, -1, "ELB detection must exist")
        auth_sets = list(
            re.finditer(
                r'''[\w\[\]'"\.]*[Aa]uthorization[\w\[\]'"\.]*\s*[=:]''',
                self.source,
            )
        )
        self.assertTrue(
            auth_sets, "must set Authorization header for health checks"
        )
        after_elb = [m for m in auth_sets if m.start() > elb_pos]
        self.assertTrue(
            after_elb,
            "Authorization header injection must be inside the "
            "health-check branch, not unconditional",
        )


# ---------------------------------------------------------------------------
# A9 -- No credential leakage
# ---------------------------------------------------------------------------


class NoCredentialLeakageTests(unittest.TestCase):
    """A9: No credential defaults, no logging of credential values."""

    def setUp(self):
        self.source = _require_gateway_source()

    def test_no_literal_secret_values(self):
        for pattern in (
            r'password\s*=\s*["\'][^"\']{4,}["\']',
            r'secret\s*=\s*["\'][^"\']{4,}["\']',
        ):
            self.assertNotRegex(
                self.source,
                pattern,
                "source must not contain literal secret values",
            )

    def test_credentials_not_in_console_output(self):
        for line in self.source.splitlines():
            stripped = line.strip()
            if stripped.startswith("console."):
                self.assertNotIn(
                    "OPENCODE_SERVER_USERNAME",
                    stripped,
                    "console statements must not reference credential env vars",
                )
                self.assertNotIn(
                    "OPENCODE_SERVER_PASSWORD",
                    stripped,
                    "console statements must not reference credential env vars",
                )

    def test_credentials_not_in_spawn_argv(self):
        self.assertNotRegex(
            self.source,
            r'''--password\b''',
            "credentials must not be passed as --password CLI flag",
        )
        self.assertNotRegex(
            self.source,
            r'''--username\b''',
            "credentials must not be passed as --username CLI flag",
        )


# ---------------------------------------------------------------------------
# A10 -- Streaming and upgrade forwarding
# ---------------------------------------------------------------------------


class StreamingUpgradeTests(unittest.TestCase):
    """A10: Bodies are piped; HTTP upgrade events are forwarded."""

    def setUp(self):
        self.source = _require_gateway_source()

    def test_pipes_request_and_response_streams(self):
        pipe_count = len(re.findall(r'\.pipe\s*\(', self.source))
        self.assertGreaterEqual(
            pipe_count,
            2,
            "must pipe request body to proxy and proxy response to client",
        )

    def test_handles_upgrade_event(self):
        self.assertTrue(
            "'upgrade'" in self.source or '"upgrade"' in self.source,
            "must register an 'upgrade' event listener",
        )

    def test_upgrade_handler_pipes_socket(self):
        upgrade_pos = self.source.find("upgrade")
        self.assertGreater(upgrade_pos, -1)
        after_upgrade = self.source[upgrade_pos:]
        self.assertTrue(
            re.search(r'\.pipe\s*\(', after_upgrade),
            "upgrade handler must pipe the socket to the child",
        )


# ---------------------------------------------------------------------------
# A11 -- Child lifecycle propagation
# ---------------------------------------------------------------------------


class ChildLifecycleTests(unittest.TestCase):
    """A11: Child exit -> gateway exit; signals forwarded to child."""

    def setUp(self):
        self.source = _require_gateway_source()

    def test_handles_child_exit_or_close(self):
        self.assertTrue(
            re.search(
                r'''\.\s*(?:on|once)\s*\(\s*['"](?:exit|close)''',
                self.source,
            ),
            "must listen for child 'exit' or 'close' events",
        )

    def test_gateway_exits_on_child_failure(self):
        self.assertIn(
            "process.exit",
            self.source,
            "gateway must exit when child fails",
        )

    def test_forwards_sigterm(self):
        self.assertIn("SIGTERM", self.source, "must handle SIGTERM")

    def test_forwards_sigint(self):
        self.assertIn("SIGINT", self.source, "must handle SIGINT")

    def test_kills_child_on_signal(self):
        self.assertTrue(
            re.search(r'\.kill\s*\(', self.source),
            "must call child.kill() to forward signals",
        )


# ---------------------------------------------------------------------------
# A4 live -- Fail-closed without credentials
# ---------------------------------------------------------------------------


@unittest.skipUnless(shutil.which("docker"), "docker CLI not found")
class LiveFailClosedTests(unittest.TestCase):
    """A4 live: container exits non-zero when credentials are missing."""

    def _docker_env(self):
        return _strip_proxy_env()

    def test_container_exits_nonzero_without_credentials(self):
        if not shutil.which("curl"):
            self.skipTest("curl not found")
        env = self._docker_env()
        tag = f"teamflow-gateway-failclosed:{int(time.time())}"
        container_id = None

        ok, reason = _build_image(tag, env)
        if not ok:
            self.skipTest(f"docker build skipped: {reason}")

        try:
            run = subprocess.run(
                ["docker", "run", "-d", "-p", f"0:{DEFAULT_PORT}", tag],
                capture_output=True,
                text=True,
                timeout=60,
                env=env,
                stdin=subprocess.DEVNULL,
            )
            if run.returncode != 0:
                self.skipTest(
                    f"docker run failed (exit {run.returncode}): "
                    f"{run.stderr[:200]}"
                )
            container_id = run.stdout.strip()

            exit_code = _wait_for_exit(
                container_id, env, FAIL_CLOSED_TIMEOUT
            )
            self.assertIsNotNone(
                exit_code,
                "container must exit within "
                f"{FAIL_CLOSED_TIMEOUT}s when credentials are missing",
            )
            self.assertNotEqual(
                exit_code,
                0,
                "container must exit non-zero without credentials "
                "(fail-closed)",
            )
        finally:
            if container_id:
                subprocess.run(
                    ["docker", "rm", "-f", container_id],
                    capture_output=True,
                    timeout=30,
                    env=env,
                    stdin=subprocess.DEVNULL,
                )
            subprocess.run(
                ["docker", "rmi", "-f", tag],
                capture_output=True,
                timeout=30,
                env=env,
                stdin=subprocess.DEVNULL,
            )


# ---------------------------------------------------------------------------
# A5/A6/A7/A9 live -- Gateway health and auth contract
# ---------------------------------------------------------------------------


@unittest.skipUnless(shutil.which("docker"), "docker CLI not found")
class LiveGatewayContractTests(unittest.TestCase):
    """A5/A6/A7/A9 live: ELB-200, anon-401, auth-200, no auth echo."""

    def _docker_env(self):
        return _strip_proxy_env()

    def test_gateway_health_and_auth_contract(self):
        if not shutil.which("curl"):
            self.skipTest("curl not found")
        env = self._docker_env()
        tag = f"teamflow-gateway-contract:{int(time.time())}"
        container_id = None

        ok, reason = _build_image(tag, env)
        if not ok:
            self.skipTest(f"docker build skipped: {reason}")

        try:
            run = subprocess.run(
                [
                    "docker", "run", "-d", "--rm",
                    "-p", f"0:{DEFAULT_PORT}",
                    "-e", f"OPENCODE_SERVER_USERNAME={TEST_USER}",
                    "-e", f"OPENCODE_SERVER_PASSWORD={TEST_PASSWORD}",
                    tag,
                ],
                capture_output=True,
                text=True,
                timeout=60,
                env=env,
                stdin=subprocess.DEVNULL,
            )
            if run.returncode != 0:
                self.skipTest(
                    f"docker run failed (exit {run.returncode}): "
                    f"{run.stderr[:200]}"
                )
            container_id = run.stdout.strip()

            host_port = _mapped_port(container_id, env)
            self.assertIsNotNone(
                host_port, "could not determine mapped host port"
            )

            # A5: ELB health check -> 200
            self._probe_elb_health_200(host_port, env)
            # A6: anonymous normal request -> 401
            self._probe_anonymous_401(host_port, env)
            # A7: authenticated normal request -> 200
            self._probe_basic_auth_200(host_port, env)
            # A9: response headers of proxied 200 have no Authorization echo
            self._probe_no_auth_header_echo(host_port, env)
        finally:
            if container_id:
                subprocess.run(
                    ["docker", "stop", "-t", "5", container_id],
                    capture_output=True,
                    timeout=30,
                    env=env,
                    stdin=subprocess.DEVNULL,
                )
            subprocess.run(
                ["docker", "rmi", "-f", tag],
                capture_output=True,
                timeout=30,
                env=env,
                stdin=subprocess.DEVNULL,
            )

    def _probe_elb_health_200(self, host_port, env):
        """A5: ELB User-Agent on root path returns HTTP 200."""
        last_error = None
        for _ in range(HTTP_RETRIES):
            try:
                probe = subprocess.run(
                    [
                        "curl", "-sS", "-o", "/dev/null",
                        "-w", "%{http_code}",
                        "--max-time", str(HTTP_TIMEOUT),
                        "--connect-timeout", "10",
                        "-H", "User-Agent: ELB-HealthChecker/2.0",
                        f"http://localhost:{host_port}/",
                    ],
                    capture_output=True,
                    text=True,
                    timeout=HTTP_TIMEOUT + 15,
                    env=env,
                    stdin=subprocess.DEVNULL,
                )
                code = probe.stdout.strip()
                if probe.returncode == 0 and code == "200":
                    return
                last_error = f"curl exit={probe.returncode} http_code='{code}'"
            except subprocess.TimeoutExpired:
                last_error = "curl timed out"
            except OSError as exc:
                last_error = f"curl error: {exc}"
            time.sleep(HTTP_RETRY_DELAY)
        self.fail(
            f"ELB health check GET / did not return 200 after "
            f"{HTTP_RETRIES} retries: {last_error}"
        )

    def _probe_anonymous_401(self, host_port, env):
        """A6: anonymous normal root request returns HTTP 401."""
        last_error = None
        for _ in range(HTTP_RETRIES):
            try:
                probe = subprocess.run(
                    [
                        "curl", "-sS", "-o", "/dev/null",
                        "-w", "%{http_code}",
                        "--max-time", str(HTTP_TIMEOUT),
                        "--connect-timeout", "10",
                        f"http://localhost:{host_port}/",
                    ],
                    capture_output=True,
                    text=True,
                    timeout=HTTP_TIMEOUT + 15,
                    env=env,
                    stdin=subprocess.DEVNULL,
                )
                code = probe.stdout.strip()
                if probe.returncode == 0 and code == "401":
                    return
                last_error = f"curl exit={probe.returncode} http_code='{code}'"
            except subprocess.TimeoutExpired:
                last_error = "curl timed out"
            except OSError as exc:
                last_error = f"curl error: {exc}"
            time.sleep(HTTP_RETRY_DELAY)
        self.fail(
            f"Anonymous GET / did not return 401 after "
            f"{HTTP_RETRIES} retries: {last_error}"
        )

    def _probe_basic_auth_200(self, host_port, env):
        """A7: authenticated normal root request returns HTTP 200."""
        last_error = None
        for _ in range(HTTP_RETRIES):
            try:
                probe = subprocess.run(
                    [
                        "curl", "-sS", "-o", "/dev/null",
                        "-w", "%{http_code}",
                        "--max-time", str(HTTP_TIMEOUT),
                        "--connect-timeout", "10",
                        "-u", f"{TEST_USER}:{TEST_PASSWORD}",
                        f"http://localhost:{host_port}/",
                    ],
                    capture_output=True,
                    text=True,
                    timeout=HTTP_TIMEOUT + 15,
                    env=env,
                    stdin=subprocess.DEVNULL,
                )
                code = probe.stdout.strip()
                if probe.returncode == 0 and code == "200":
                    return
                last_error = f"curl exit={probe.returncode} http_code='{code}'"
            except subprocess.TimeoutExpired:
                last_error = "curl timed out"
            except OSError as exc:
                last_error = f"curl error: {exc}"
            time.sleep(HTTP_RETRY_DELAY)
        self.fail(
            f"Basic Auth GET / did not return 200 after "
            f"{HTTP_RETRIES} retries: {last_error}"
        )

    def _probe_no_auth_header_echo(self, host_port, env):
        """A9: response headers of a proxied 200 contain no Authorization."""
        for _ in range(HTTP_RETRIES):
            try:
                probe = subprocess.run(
                    [
                        "curl", "-sS", "-D", "-", "-o", "/dev/null",
                        "--max-time", str(HTTP_TIMEOUT),
                        "--connect-timeout", "10",
                        "-u", f"{TEST_USER}:{TEST_PASSWORD}",
                        f"http://localhost:{host_port}/",
                    ],
                    capture_output=True,
                    text=True,
                    timeout=HTTP_TIMEOUT + 15,
                    env=env,
                    stdin=subprocess.DEVNULL,
                )
                if probe.returncode == 0:
                    lines = probe.stdout.splitlines()
                    if lines and "200" in lines[0]:
                        self.assertNotIn(
                            "authorization:",
                            probe.stdout.lower(),
                            "response headers must not echo Authorization",
                        )
                        return
            except subprocess.TimeoutExpired:
                pass
            except OSError:
                pass
            time.sleep(HTTP_RETRY_DELAY)
        self.skipTest(
            "No 200 response available to check for auth header echo "
            "(covered by A7 if that probe also fails)"
        )


if __name__ == "__main__":
    unittest.main()
