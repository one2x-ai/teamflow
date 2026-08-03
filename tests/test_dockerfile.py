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

#: Internal loopback port the container listens on (Caddy sidecar owns 3000).
DEFAULT_PORT = 13000

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
# AC 2 — Direct OpenCode workload (loopback, no gateway)
# ---------------------------------------------------------------------------
#
# Under the container-sidecar contract, the Dockerfile CMD directly
# executes ``opencode web`` (exec-form JSON array) binding to
# ``127.0.0.1:13000`` (loopback only).  There is no shell supervisor,
# no Node gateway, and no second process.  The public port 3000 is
# owned by the Caddy sidecar in the same Pod, which reverse-proxies to
# this container.


class CommandContractTests(unittest.TestCase):
    """AC 2: CMD directly invokes ``opencode web`` on loopback 127.0.0.1:13000."""

    def setUp(self):
        self.text = _require_dockerfile_text()

    def test_cmd_is_exec_form(self):
        """A1: CMD is exec-form (JSON array), not shell-form string."""
        lines = _logical_lines(self.text)
        cmds = [ln for ln in lines if ln.upper().startswith("CMD")]
        self.assertTrue(cmds, "Dockerfile must contain a CMD instruction")
        last_cmd = cmds[-1]
        self.assertTrue(
            last_cmd.strip().startswith("CMD ["),
            "CMD must be exec-form (JSON array [ ... ]), not shell-form",
        )

    def test_cmd_invokes_opencode_web(self):
        """A2: CMD directly invokes ``opencode web``."""
        cmd = _final_command(self.text)
        self.assertIn("opencode", cmd, "CMD must invoke 'opencode'")
        self.assertIn("web", cmd, "CMD must include the 'web' subcommand")

    def test_cmd_binds_loopback_hostname(self):
        """A3: CMD includes ``--hostname 127.0.0.1`` (loopback only)."""
        cmd = _final_command(self.text)
        self.assertIn("127.0.0.1", cmd)

    def test_cmd_uses_port_13000(self):
        """A4: CMD includes ``--port 13000``."""
        cmd = _final_command(self.text)
        self.assertIn("13000", cmd)

    def test_cmd_does_not_invoke_node(self):
        """A5: CMD must NOT invoke ``node``."""
        cmd = _final_command(self.text)
        self.assertNotRegex(cmd, r"\bnode\b", "CMD must not invoke 'node'")

    def test_cmd_does_not_reference_gateway_script(self):
        """A6: CMD must NOT reference the gateway launcher filename."""
        cmd = _final_command(self.text)
        self.assertNotIn(
            GATEWAY_SCRIPT_NAME,
            cmd,
            "CMD must not reference the retired gateway launcher",
        )

    def test_cmd_does_not_bind_public_address(self):
        """A7: CMD must NOT contain ``0.0.0.0`` (no public bind)."""
        cmd = _final_command(self.text)
        self.assertNotIn("0.0.0.0", cmd)

    def test_expose_is_13000(self):
        """A8: EXPOSE is 13000 (the internal loopback port)."""
        expose = re.search(r"^\s*EXPOSE\s+(\d+)", self.text, re.MULTILINE)
        self.assertIsNotNone(expose, "No EXPOSE instruction found")
        self.assertEqual(
            int(expose.group(1)),
            DEFAULT_PORT,
            f"EXPOSE must be {DEFAULT_PORT} (internal loopback port)",
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
        self.assertRegex(
            self.text,
            r'git\s+clone',
            "Dockerfile must clone the repository via 'git clone' instead "
            "of COPY . .",
        )
        self.assertIn(
            "/workspace/teamflow",
            self.text,
            "Dockerfile must clone the repository to /workspace/teamflow",
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

    def test_installs_pinned_official_bun_in_final_runtime(self):
        final_stage = re.split(r"(?im)^\s*FROM\s+", self.text)[-1]
        self.assertRegex(
            final_stage,
            r"(?im)^\s*COPY\s+--from=oven/bun:\d+\.\d+\.\d+\s+"
            r"/usr/local/bin/bun\s+/usr/local/bin/bun\s*$",
            "final runtime stage must copy Bun from a pinned official image",
        )
        self.assertNotRegex(
            final_stage,
            r"(?i)oven/bun:(?:latest|1)(?:\s|$)",
            "Bun source must use a complete pinned release tag",
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
# AC 10 — RETIRED: Live auth contract (public-port probe)
# ---------------------------------------------------------------------------
#
# The former ``LiveAuthContractTests`` class probed the container's
# PUBLIC port 3000 directly via ``docker run -p``.  Under the
# container-sidecar contract, the container binds loopback
# 127.0.0.1:13000 and owns no public endpoint.  The public port 3000
# is owned by the Caddy sidecar in the same Pod, which performs
# health-probe synthesis (kube-probe / ELB-HealthChecker UA → synthetic
# ``ok``) and Basic-Auth forwarding.  That behaviour is documented in
# ``docs/container-sidecar-deployment.md`` and is NOT reimplemented in
# Teamflow code.  The class and its private helpers (``_mapped_port``,
# ``_probe_anonymous_401``, ``_probe_basic_auth_200``) have been
# removed.  No weaker substitute is introduced.


# ---------------------------------------------------------------------------
# AC 11 — Workspace git checkout contract
# ---------------------------------------------------------------------------


class WorkspaceGitCheckoutTests(unittest.TestCase):
    """The image contains a valid Git checkout at /workspace/teamflow."""

    def setUp(self):
        self.text = _require_dockerfile_text()

    def test_dockerfile_references_workspace_teamflow(self):
        self.assertIn(
            "/workspace/teamflow",
            self.text,
            "Dockerfile must reference the path /workspace/teamflow",
        )

    def test_dockerfile_contains_git_clone(self):
        self.assertRegex(
            self.text,
            r'git\s+clone',
            "Dockerfile must contain a 'git clone' instruction",
        )

    def test_final_workdir_is_workspace_teamflow(self):
        workdirs = re.findall(r"(?im)^\s*WORKDIR\s+(\S+)", self.text)
        self.assertTrue(workdirs, "Dockerfile must set WORKDIR")
        final_wd = workdirs[-1].strip('"').strip("'")
        self.assertEqual(
            final_wd,
            "/workspace/teamflow",
            "final WORKDIR must resolve to /workspace/teamflow, "
            f"got '{final_wd}'",
        )

    def test_final_workdir_is_not_app(self):
        workdirs = re.findall(r"(?im)^\s*WORKDIR\s+(\S+)", self.text)
        self.assertTrue(workdirs, "Dockerfile must set WORKDIR")
        final_wd = workdirs[-1].strip('"').strip("'")
        self.assertNotEqual(
            final_wd,
            "/app",
            "final WORKDIR must not be /app (old COPY pattern replaced)",
        )


# ---------------------------------------------------------------------------
# AC 12 — Deterministic git checkout source and revision
# ---------------------------------------------------------------------------


class DeterministicCheckoutTests(unittest.TestCase):
    """Source and revision are observable and deterministic."""

    def setUp(self):
        self.text = _require_dockerfile_text()

    def test_teamflow_repo_url_arg_with_github_default(self):
        m = re.search(
            r"(?im)^\s*ARG\s+TEAMFLOW_REPO_URL\s*=\s*(\S+)",
            self.text,
        )
        self.assertIsNotNone(
            m,
            "Dockerfile must declare ARG TEAMFLOW_REPO_URL with a default",
        )
        self.assertIn(
            "github.com",
            m.group(1),
            "TEAMFLOW_REPO_URL default must reference github.com "
            "(public repo, no credentials)",
        )

    def test_teamflow_repo_ref_arg_exists(self):
        self.assertRegex(
            self.text,
            r"(?im)^\s*ARG\s+TEAMFLOW_REPO_REF\b",
            "Dockerfile must declare ARG TEAMFLOW_REPO_REF",
        )

    def test_teamflow_repo_ref_default_is_main_branch(self):
        m = re.search(
            r"(?im)^\s*ARG\s+TEAMFLOW_REPO_REF\s*=\s*(\S+)",
            self.text,
        )
        self.assertIsNotNone(
            m,
            "Dockerfile must declare ARG TEAMFLOW_REPO_REF with a default",
        )
        default_val = m.group(1).strip().strip('"').strip("'")
        self.assertEqual(
            default_val,
            "main",
            "TEAMFLOW_REPO_REF must default to the repository main branch "
            f"(got '{default_val}')",
        )

    def test_no_literal_github_credentials(self):
        self.assertNotRegex(
            self.text,
            r"ghp_",
            "Dockerfile must not contain GitHub PAT tokens (ghp_)",
        )
        self.assertNotRegex(
            self.text,
            r"token=",
            "Dockerfile must not contain token= credential patterns",
        )
        self.assertNotRegex(
            self.text,
            r"password=",
            "Dockerfile must not contain password= credential patterns",
        )


# ---------------------------------------------------------------------------
# AC 13 — Workspace ownership for the non-root user
# ---------------------------------------------------------------------------


class WorkspaceOwnershipTests(unittest.TestCase):
    """Checkout is owned by the non-root user."""

    def setUp(self):
        self.text = _require_dockerfile_text()

    def test_chown_applied_to_workspace(self):
        chown_lines = [
            line for line in self.text.splitlines()
            if re.search(r"chown", line, re.IGNORECASE)
        ]
        found = any(
            "opencode" in line and "/workspace" in line
            for line in chown_lines
        )
        self.assertTrue(
            found,
            "Dockerfile must chown /workspace to opencode (non-root user "
            "must own the checkout)",
        )

    def test_useradd_uid_1001(self):
        self.assertRegex(
            self.text,
            r"useradd.*--uid\s+1001",
            "useradd must specify --uid 1001",
        )

    def test_groupadd_gid_1001(self):
        self.assertRegex(
            self.text,
            r"groupadd.*--gid\s+1001",
            "groupadd must specify --gid 1001",
        )


# ---------------------------------------------------------------------------
# AC 14 — RETIRED: Gateway launcher at an immutable path outside the workspace
# ---------------------------------------------------------------------------
#
# The former ``RuntimeLauncherLocationTests`` class asserted that the
# Dockerfile COPYs ``scripts/opencode_health_gateway.js`` to an immutable
# out-of-workspace path and that CMD references the absolute path
# (``/opt/teamflow-runtime/scripts/opencode_health_gateway.js``).
# This contract is retired because ``scripts/opencode_health_gateway.js``
# was removed.  The container now runs ``opencode web`` directly with no
# launcher at an immutable out-of-workspace path.  No weaker substitute
# is introduced.


# ---------------------------------------------------------------------------
# AC 15 — README documents the workspace contract
# ---------------------------------------------------------------------------


class WorkspaceReadmeDocTests(unittest.TestCase):
    """README documents the workspace contract."""

    def setUp(self):
        readme = ROOT / "README.md"
        if not readme.exists():
            raise AssertionError(f"README.md not found at {readme}")
        self.text = readme.read_text(encoding="utf-8")

    def test_readme_references_workspace_teamflow(self):
        self.assertIn(
            "/workspace/teamflow",
            self.text,
            "README must document the /workspace/teamflow checkout path",
        )

    # NOTE: ``test_readme_documents_playground_workspace_persistence`` and
    # ``test_readme_documents_standalone_volume_requirement`` were retired
    # from this README test because the PVC / Pod-preservation and standalone
    # deployment detail moved out of the README into the deployment design
    # doc ``docs/container-sidecar-deployment.md`` (see C6 deployment-doc
    # content assertions in ``tests/test_readme_docs_contract.py``).  The
    # content is still asserted — it was not weakened, just relocated.


if __name__ == "__main__":
    unittest.main()
