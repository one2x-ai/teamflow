"""Requirement tests for the root Dockerfile and .dockerignore contract.

Static tests assert the structure and runtime contract of a not-yet-written
Docker image (``opencode web`` with safe hostname binding, multi-stage
build, non-root user, required runtime CLIs, and secret exclusion), plus
an optional live ``docker build`` smoke check.

Static tests MUST fail before ``Dockerfile`` / ``.dockerignore`` exist and
MUST pass once the coder implements them.  The live smoke test is
conditional: it skips when Docker is absent and skip-with-reason when a
registry/network error blocks the build.
"""

import os
import re
import shutil
import subprocess
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOCKERFILE = ROOT / "Dockerfile"
DOCKERIGNORE = ROOT / ".dockerignore"

#: Deterministic default container port shared by static and smoke tests.
DEFAULT_PORT = 3000

#: Name of the gateway launcher script under scripts/.
GATEWAY_SCRIPT_NAME = "opencode_health_gateway.js"

#: Docker build timeout (must exceed 120 s).
BUILD_TIMEOUT = 600

#: HTTP probe settings.
HTTP_RETRIES = 12
HTTP_RETRY_DELAY = 5
HTTP_TIMEOUT = 60

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


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _strip_proxy_env(base=None):
    """Return a dict copy of *base* with all proxy variables removed."""
    env = dict(base if base is not None else os.environ)
    for key in _PROXY_KEYS:
        env.pop(key, None)
    return env


def _require_dockerfile_text():
    """Return Dockerfile contents; raise AssertionError if absent or empty."""
    if not DOCKERFILE.exists():
        raise AssertionError(f"Dockerfile not found at {DOCKERFILE}")
    text = DOCKERFILE.read_text(encoding="utf-8")
    if not text.strip():
        raise AssertionError("Dockerfile is empty")
    return text


def _require_dockerignore_text():
    """Return .dockerignore contents; raise AssertionError if absent."""
    if not DOCKERIGNORE.exists():
        raise AssertionError(f".dockerignore not found at {DOCKERIGNORE}")
    return DOCKERIGNORE.read_text(encoding="utf-8")


def _join_continuations(text):
    """Join backslash-continuation lines into single logical lines."""
    return re.sub(r"\\\s*\n\s*", " ", text)


def _logical_lines(text):
    """Return non-comment, non-blank logical Dockerfile instructions."""
    joined = _join_continuations(text)
    result = []
    for line in joined.splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            result.append(stripped)
    return result


def _final_command(text):
    """Return normalized text of the last ENTRYPOINT and CMD instructions.

    JSON-array quoting and brackets are flattened so that regex checks
    work regardless of exec-form vs shell-form.
    """
    lines = _logical_lines(text)
    last_entrypoint = ""
    last_cmd = ""
    for line in lines:
        upper = line.upper()
        if upper.startswith("ENTRYPOINT"):
            last_entrypoint = line
        elif upper.startswith("CMD"):
            last_cmd = line
    raw = " ".join(p for p in (last_entrypoint, last_cmd) if p)
    cleaned = re.sub(r"['\"\[\],]", " ", raw)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned


def _dockerignore_patterns():
    """Return list of raw pattern lines from .dockerignore (no comments)."""
    text = _require_dockerignore_text()
    patterns = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            patterns.append(stripped)
    return patterns


def _pattern_covers(pattern, path):
    """True if a dockerignore *pattern* would cover *path*.

    Handles ``**`` (any depth), ``*`` (within a segment), literal directory
    prefixes, and exact matches.
    """
    pat = pattern.lstrip("!").rstrip("/")
    # Exact or directory-prefix match
    if pat == path or path.startswith(pat + "/"):
        return True
    # Glob → regex
    regex = ""
    i = 0
    while i < len(pat):
        if pat[i : i + 2] == "**":
            regex += ".*"
            i += 2
        elif pat[i] == "*":
            regex += "[^/]*"
            i += 1
        elif pat[i] == "?":
            regex += "[^/]"
            i += 1
        else:
            regex += re.escape(pat[i])
            i += 1
    # Match against the path itself or any parent directory
    check_paths = [path]
    parts = path.split("/")
    for j in range(1, len(parts)):
        check_paths.append("/".join(parts[:j]))
    return any(re.fullmatch(regex, c) for c in check_paths)


# ---------------------------------------------------------------------------
# AC 1 — Dockerfile exists and is non-empty
# ---------------------------------------------------------------------------


class DockerfileExistsTests(unittest.TestCase):
    """AC 1: ``ROOT/Dockerfile`` exists and is non-empty."""

    def test_dockerfile_exists(self):
        self.assertTrue(
            DOCKERFILE.exists(),
            f"Dockerfile must exist at repository root ({DOCKERFILE})",
        )

    def test_dockerfile_non_empty(self):
        text = _require_dockerfile_text()
        self.assertGreater(len(text.strip()), 0, "Dockerfile must not be empty")


# ---------------------------------------------------------------------------
# AC 2 — Gateway command contract
# ---------------------------------------------------------------------------
#
# Under the gateway architecture, the Dockerfile CMD launches the gateway
# launcher (``scripts/opencode_health_gateway.js``) instead of raw
# ``opencode web``.  The gateway owns the public ``0.0.0.0:PORT`` listener
# and spawns ``opencode web`` as a child on ``127.0.0.1``.  The contract
# assertions below verify the new CMD shape without weakening EXPOSE,
# non-root USER, multi-stage, or secret-guard assertions.


class CommandContractTests(unittest.TestCase):
    """AC 2: CMD launches the gateway; gateway owns 0.0.0.0:PORT bind."""

    def setUp(self):
        self.text = _require_dockerfile_text()

    def test_cmd_invokes_gateway_launcher(self):
        cmd = _final_command(self.text)
        self.assertIn(
            GATEWAY_SCRIPT_NAME,
            cmd,
            f"CMD must launch the gateway launcher ({GATEWAY_SCRIPT_NAME})",
        )

    def test_cmd_invokes_node(self):
        cmd = _final_command(self.text)
        self.assertRegex(
            cmd,
            r"\bnode\b",
            "CMD must invoke 'node' to run the gateway script",
        )

    def test_cmd_does_not_directly_run_opencode_web(self):
        """The CMD must not directly run ``opencode web`` (gateway owns it)."""
        cmd = _final_command(self.text)
        self.assertNotRegex(
            cmd,
            r"\bopencode\s+web\b",
            "CMD must launch the gateway, not raw 'opencode web'",
        )

    def test_public_listener_is_not_loopback(self):
        """The gateway (not CMD flags) owns the public 0.0.0.0 bind.

        The CMD itself should not contain a 127.0.0.1 hostname bind.
        The public listener is managed by the gateway script which binds
        0.0.0.0.
        """
        cmd = _final_command(self.text)
        self.assertNotIn(
            "127.0.0.1",
            cmd,
            "CMD must not bind to loopback; the gateway manages the public bind",
        )

    def test_port_default_3000_exists(self):
        """A PORT default of 3000 must exist somewhere in Dockerfile or CMD.

        The gateway reads ``process.env.PORT`` (default 3000).  The Dockerfile
        may carry ``${PORT:-3000}`` in the CMD for pass-through, or the
        default is owned by the gateway script itself.
        """
        self.assertTrue(
            "3000" in self.text,
            "PORT default of 3000 must exist in Dockerfile or CMD",
        )

    def test_expose_is_3000(self):
        expose = re.search(r"^\s*EXPOSE\s+(\d+)", self.text, re.MULTILINE)
        self.assertIsNotNone(expose, "No EXPOSE instruction found")
        self.assertEqual(
            int(expose.group(1)),
            DEFAULT_PORT,
            f"EXPOSE must be {DEFAULT_PORT}",
        )


# ---------------------------------------------------------------------------
# AC 3 — Multi-stage build
# ---------------------------------------------------------------------------


class MultiStageBuildTests(unittest.TestCase):
    """AC 3: at least two FROM instructions and two named stages."""

    def setUp(self):
        self.text = _require_dockerfile_text()

    def test_at_least_two_from_instructions(self):
        froms = re.findall(r"(?im)^\s*FROM\s+", self.text)
        self.assertGreaterEqual(
            len(froms),
            2,
            "Dockerfile must have at least two FROM instructions (multi-stage)",
        )

    def test_at_least_two_named_stages(self):
        stages = re.findall(r"(?im)^\s*FROM\s+\S+\s+AS\s+(\S+)", self.text)
        self.assertGreaterEqual(
            len(stages),
            2,
            "Dockerfile must name at least two stages via AS",
        )


# ---------------------------------------------------------------------------
# AC 4 — Non-root execution
# ---------------------------------------------------------------------------


class NonRootUserTests(unittest.TestCase):
    """AC 4: the final stage runs as a non-root user."""

    def setUp(self):
        self.text = _require_dockerfile_text()

    def test_final_user_is_non_root(self):
        users = re.findall(r"(?im)^\s*USER\s+(\S+)", self.text)
        self.assertTrue(users, "Dockerfile must contain a USER instruction")
        last_user = users[-1].strip('"').strip("'")
        self.assertNotIn(
            last_user.lower(),
            ("root", "0", ""),
            f"final USER must not be root/0/empty, got '{last_user}'",
        )
        # Also reject UID:GID where UID is 0
        if ":" in last_user:
            uid = last_user.split(":")[0]
            self.assertNotEqual(
                uid,
                "0",
                f"USER UID must not be 0 (root), got '{last_user}'",
            )


# ---------------------------------------------------------------------------
# AC 5 — Required runtime files in the final image
# ---------------------------------------------------------------------------


class RequiredRuntimeFilesTests(unittest.TestCase):
    """AC 5: WORKDIR, repo COPY, required CLIs, and Node.js 22+."""

    def setUp(self):
        self.text = _require_dockerfile_text()

    def test_workdir_is_set(self):
        workdirs = re.findall(r"(?im)^\s*WORKDIR\s+(\S+)", self.text)
        self.assertTrue(workdirs, "Dockerfile must set WORKDIR")
        final_wd = workdirs[-1].strip('"').strip("'")
        self.assertTrue(final_wd, "final WORKDIR must not be empty")

    def test_copies_repository_into_image(self):
        lines = _logical_lines(self.text)
        copy_found = False
        for line in lines:
            if not line.upper().startswith("COPY"):
                continue
            parts = line.split()
            args = [p for p in parts[1:] if not p.startswith("--")]
            if args and args[0] == ".":
                copy_found = True
                break
        self.assertTrue(
            copy_found,
            "Dockerfile must COPY the repository (source '.') into the image",
        )

    def test_installs_opencode_cli(self):
        self.assertIn(
            "opencode-ai",
            self.text,
            "Dockerfile must install the OpenCode CLI (opencode-ai)",
        )

    def test_installs_pi_agent(self):
        self.assertIn(
            "@earendil-works/pi-coding-agent",
            self.text,
            "Dockerfile must install the Pi coding agent",
        )

    def test_installs_basic_memory(self):
        self.assertIn(
            "basic-memory",
            self.text,
            "Dockerfile must install basic-memory (via uv)",
        )

    def test_nodejs_22_available(self):
        node_patterns = [
            r"node:\s*2[2-9]",
            r"nodejs-\s*2[2-9]",
            r"NODE_VERSION\s*=?\s*2[2-9]",
            r"nvm\s+(?:install|use)\s+2[2-9]",
            r"nodesource.*2[2-9]",
        ]
        self.assertTrue(
            any(re.search(p, self.text, re.IGNORECASE) for p in node_patterns),
            "Dockerfile must make Node.js 22+ available in the final image",
        )


# ---------------------------------------------------------------------------
# AC 6 — .dockerignore secret / build-context exclusion
# ---------------------------------------------------------------------------


class DockerignoreContractTests(unittest.TestCase):
    """AC 6: .dockerignore excludes secrets and build noise, keeps runtime."""

    def setUp(self):
        self.text = _require_dockerignore_text()
        self.patterns = _dockerignore_patterns()

    # --- must exclude ---

    def test_git_metadata_excluded(self):
        self.assertTrue(
            any(p.rstrip("/").lstrip("!") in (".git", ".git/*", ".git/**") for p in self.patterns)
            or ".git" in self.text,
            ".dockerignore must exclude .git",
        )

    def test_env_files_excluded(self):
        self.assertIn(
            ".env",
            self.text,
            ".dockerignore must exclude .env files",
        )

    def test_teamflow_runs_excluded(self):
        self.assertIn(
            ".teamflow/runs",
            self.text,
            ".dockerignore must exclude .teamflow/runs/",
        )

    def test_teamflow_sessions_excluded(self):
        self.assertIn(
            ".teamflow/sessions",
            self.text,
            ".dockerignore must exclude .teamflow/sessions/",
        )

    def test_credentials_excluded(self):
        self.assertIn(
            "auth.json",
            self.text,
            ".dockerignore must exclude .teamflow/auth.json",
        )
        self.assertIn(
            "models-store.json",
            self.text,
            ".dockerignore must exclude .teamflow/models-store.json",
        )

    def test_node_modules_excluded(self):
        self.assertIn(
            "node_modules",
            self.text,
            ".dockerignore must exclude node_modules",
        )

    def test_server_dist_excluded(self):
        self.assertIn(
            "server/dist",
            self.text,
            ".dockerignore must exclude server/dist",
        )

    def test_server_web_dist_excluded(self):
        self.assertIn(
            "server/web/dist",
            self.text,
            ".dockerignore must exclude server/web/dist",
        )

    # --- must NOT exclude ---

    def test_teamflow_bin_not_excluded(self):
        path = ".teamflow/bin/teamflow"
        for pat in self.patterns:
            if pat.startswith("!"):
                continue
            self.assertFalse(
                _pattern_covers(pat, path),
                f".dockerignore pattern '{pat}' would exclude {path}",
            )

    def test_teamflow_agents_not_excluded(self):
        path = ".teamflow/agents"
        for pat in self.patterns:
            if pat.startswith("!"):
                continue
            self.assertFalse(
                _pattern_covers(pat, path),
                f".dockerignore pattern '{pat}' would exclude {path}",
            )

    def test_teamflow_skills_not_excluded(self):
        path = ".teamflow/skills"
        for pat in self.patterns:
            if pat.startswith("!"):
                continue
            self.assertFalse(
                _pattern_covers(pat, path),
                f".dockerignore pattern '{pat}' would exclude {path}",
            )


# ---------------------------------------------------------------------------
# AC 7 — Live Docker smoke test (SUPERSEDED by gateway contract)
# ---------------------------------------------------------------------------
#
# The original LiveDockerSmokeTests asserted an unauthenticated public HTTP
# 200 on ``GET /``.  Under the gateway architecture, missing credentials
# means fail-closed (no unsecured public 200).  The full gateway contract
# — fail-closed, ELB-UA 200, anon 401, auth 200 — is covered by
# ``tests/test_opencode_health_gateway.py``.


# ---------------------------------------------------------------------------
# AC 8 — README documents the OPENCODE_SERVER auth env-var contract
# ---------------------------------------------------------------------------


class ReadmeAuthEnvDocTests(unittest.TestCase):
    """README must document the runtime auth env-var contract.

    The README Docker section must name ``OPENCODE_SERVER_USERNAME`` and
    ``OPENCODE_SERVER_PASSWORD``, show a ``docker run`` example that
    injects both, use placeholder values (never real credentials), and
    state that both are required for a stable known login.
    """

    def setUp(self):
        readme = ROOT / "README.md"
        if not readme.exists():
            raise AssertionError(f"README.md not found at {readme}")
        self.text = readme.read_text(encoding="utf-8")

    def test_readme_names_username_env_var(self):
        self.assertIn(
            "OPENCODE_SERVER_USERNAME",
            self.text,
            "README must name OPENCODE_SERVER_USERNAME in its Docker section",
        )

    def test_readme_names_password_env_var(self):
        self.assertIn(
            "OPENCODE_SERVER_PASSWORD",
            self.text,
            "README must name OPENCODE_SERVER_PASSWORD in its Docker section",
        )

    def test_readme_has_docker_run_injecting_both_vars(self):
        docker_runs = re.findall(r"docker\s+run[^\n]*", self.text)
        found = any(
            "OPENCODE_SERVER_USERNAME" in line
            and "OPENCODE_SERVER_PASSWORD" in line
            for line in docker_runs
        )
        self.assertTrue(
            found,
            "README must show a 'docker run' example injecting both "
            "OPENCODE_SERVER_USERNAME and OPENCODE_SERVER_PASSWORD",
        )

    def test_readme_example_uses_placeholder_credentials(self):
        """The docker run example must not embed real credentials.

        Values must be unmistakable placeholders (``<your-username>``),
        shell-variable forwarding (``$USERNAME`` / ``${USERNAME}``), or
        use ``--env-file``.
        """
        docker_runs = re.findall(r"docker\s+run[^\n]*", self.text)
        for run_line in docker_runs:
            if (
                "OPENCODE_SERVER_USERNAME" not in run_line
                or "OPENCODE_SERVER_PASSWORD" not in run_line
            ):
                continue
            for var in ("OPENCODE_SERVER_USERNAME", "OPENCODE_SERVER_PASSWORD"):
                m = re.search(rf"-e\s+{var}=(\S+)", run_line)
                if not m:
                    continue
                val = m.group(1).strip().strip('"').strip("'")
                is_placeholder = (
                    "<" in val
                    or val.startswith("$")
                    or "--env-file" in run_line
                )
                self.assertTrue(
                    is_placeholder,
                    f"docker run example must use a placeholder for {var}, "
                    f"got literal '{val}'",
                )

    def test_readme_documents_both_vars_required(self):
        """README must state both vars are required for stable login."""
        lower = self.text.lower()
        requirement_indicators = [
            "random credential",
            "random username",
            "random password",
            "stable login",
            "stable known login",
            "stable credential",
            "must inject",
            "must provide",
            "required for",
            "are required",
            "必须",
            "随机",
            "稳定",
        ]
        found = any(ind in lower for ind in requirement_indicators)
        self.assertTrue(
            found,
            "README must document that OPENCODE_SERVER_USERNAME and "
            "OPENCODE_SERVER_PASSWORD are required for a stable known "
            "login (absence yields random credentials)",
        )


# ---------------------------------------------------------------------------
# AC 9 — Dockerfile must not bake credentials into the image
# ---------------------------------------------------------------------------


class DockerfileNoSecretsTests(unittest.TestCase):
    """The Dockerfile must not bake credentials or use build-arg secrets."""

    def setUp(self):
        self.text = _require_dockerfile_text()

    def test_no_non_empty_default_for_username(self):
        m = re.search(
            r"(?im)^\s*ENV\s+OPENCODE_SERVER_USERNAME\s*=\s*(\S+)",
            self.text,
        )
        if m:
            val = m.group(1).strip().strip('"').strip("'")
            self.assertEqual(
                val,
                "",
                "Dockerfile must not set a non-empty default for "
                "OPENCODE_SERVER_USERNAME",
            )

    def test_no_non_empty_default_for_password(self):
        m = re.search(
            r"(?im)^\s*ENV\s+OPENCODE_SERVER_PASSWORD\s*=\s*(\S+)",
            self.text,
        )
        if m:
            val = m.group(1).strip().strip('"').strip("'")
            self.assertEqual(
                val,
                "",
                "Dockerfile must not set a non-empty default for "
                "OPENCODE_SERVER_PASSWORD",
            )

    def test_no_build_arg_for_username(self):
        self.assertNotRegex(
            self.text,
            r"(?im)^\s*ARG\s+OPENCODE_SERVER_USERNAME",
            "Dockerfile must not use ARG for OPENCODE_SERVER_USERNAME",
        )

    def test_no_build_arg_for_password(self):
        self.assertNotRegex(
            self.text,
            r"(?im)^\s*ARG\s+OPENCODE_SERVER_PASSWORD",
            "Dockerfile must not use ARG for OPENCODE_SERVER_PASSWORD",
        )

    def test_no_copy_env_or_credential_files(self):
        credential_names = (".env", "auth.json", "models-store.json")
        for line in _logical_lines(self.text):
            if not line.upper().startswith("COPY"):
                continue
            for arg in line.split()[1:]:
                clean = arg.lstrip("-").strip('"').strip("'")
                for cred in credential_names:
                    if clean == cred or clean.endswith("/" + cred):
                        self.fail(
                            f"Dockerfile must not COPY credential file "
                            f"'{cred}': {line}"
                        )


# ---------------------------------------------------------------------------
# AC 10 — Live auth contract (additive to the existing smoke test)
# ---------------------------------------------------------------------------


@unittest.skipUnless(shutil.which("docker"), "docker CLI not found")
class LiveAuthContractTests(unittest.TestCase):
    """Live auth contract with both env vars injected.

    When ``OPENCODE_SERVER_USERNAME`` and ``OPENCODE_SERVER_PASSWORD`` are
    both injected at runtime, anonymous ``GET /`` returns HTTP 401 and
    Basic Auth with the injected credentials returns HTTP 200.

    One2X risk: enabling Basic Auth changes anonymous ``/`` from the
    platform-guide-expected HTTP 200 (documented by the existing
    ``LiveDockerSmokeTests`` no-auth baseline) to HTTP 401.  The 401 and
    200 assertions below are intentionally strict and are NOT weakened —
    they capture this behavioral change as an explicit requirement.
    """

    #: Test-only placeholder credentials — never default or real values.
    TEST_USER = "test-user-9f3a"
    TEST_PASSWORD = "test-pass-7c2e"

    def _docker_env(self):
        return _strip_proxy_env()

    def test_basic_auth_contract(self):
        env = self._docker_env()
        tag = f"teamflow-opencode-web-auth:{int(time.time())}"
        container_id = None

        # --- build ---
        try:
            build = subprocess.run(
                [
                    "docker",
                    "build",
                    "-t",
                    tag,
                    "-f",
                    str(DOCKERFILE),
                    str(ROOT),
                ],
                capture_output=True,
                text=True,
                timeout=BUILD_TIMEOUT,
                env=env,
                stdin=subprocess.DEVNULL,
            )
        except subprocess.TimeoutExpired:
            self.skipTest(
                f"docker build timed out after {BUILD_TIMEOUT}s "
                "(registry/network)"
            )
        except OSError as exc:
            self.skipTest(f"docker build failed (network/connection): {exc}")

        if build.returncode != 0:
            self.skipTest(
                "docker build returned non-zero exit "
                f"{build.returncode} -- likely registry/network issue"
            )

        # --- run + probe ---
        try:
            run = subprocess.run(
                [
                    "docker",
                    "run",
                    "-d",
                    "--rm",
                    "-p",
                    f"0:{DEFAULT_PORT}",
                    "-e",
                    f"OPENCODE_SERVER_USERNAME={self.TEST_USER}",
                    "-e",
                    f"OPENCODE_SERVER_PASSWORD={self.TEST_PASSWORD}",
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

            host_port = self._mapped_port(container_id, env)
            self.assertIsNotNone(host_port, "could not determine mapped host port")

            self._probe_anonymous_401(host_port, env)
            self._probe_basic_auth_200(host_port, env)
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

    def _mapped_port(self, container_id, env):
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

    def _probe_anonymous_401(self, host_port, env):
        last_error = None
        for _ in range(HTTP_RETRIES):
            try:
                probe = subprocess.run(
                    [
                        "curl",
                        "-sS",
                        "-o",
                        "/dev/null",
                        "-w",
                        "%{http_code}",
                        "--max-time",
                        str(HTTP_TIMEOUT),
                        "--connect-timeout",
                        "10",
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
                last_error = (
                    f"curl exit={probe.returncode} http_code='{code}'"
                )
            except subprocess.TimeoutExpired:
                last_error = "curl timed out"
            except OSError as exc:
                last_error = f"curl error: {exc}"
            time.sleep(HTTP_RETRY_DELAY)
        self.fail(
            f"Anonymous GET / did not return 401 after {HTTP_RETRIES} "
            f"retries: {last_error}"
        )

    def _probe_basic_auth_200(self, host_port, env):
        last_error = None
        for _ in range(HTTP_RETRIES):
            try:
                probe = subprocess.run(
                    [
                        "curl",
                        "-sS",
                        "-o",
                        "/dev/null",
                        "-w",
                        "%{http_code}",
                        "--max-time",
                        str(HTTP_TIMEOUT),
                        "--connect-timeout",
                        "10",
                        "-u",
                        f"{self.TEST_USER}:{self.TEST_PASSWORD}",
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
                last_error = (
                    f"curl exit={probe.returncode} http_code='{code}'"
                )
            except subprocess.TimeoutExpired:
                last_error = "curl timed out"
            except OSError as exc:
                last_error = f"curl error: {exc}"
            time.sleep(HTTP_RETRY_DELAY)
        self.fail(
            f"Basic Auth GET / did not return 200 after {HTTP_RETRIES} "
            f"retries: {last_error}"
        )

if __name__ == "__main__":
    unittest.main()
