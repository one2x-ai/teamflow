"""Requirement tests for run-id ``pi-inner-loop-wrapper``.

These tests pin the contract of the Pi-integration wrapper: a functional
``teamflow`` wrapper that maps roles to real ``pi`` arguments, resolves role
identity from agent Markdown frontmatter, provides local ``debug``/``session``
compatibility, and ships a hermetic installer footprint.

All paths are relative to the repository root
``ROOT = Path(__file__).resolve().parents[1]``.
"""

import json
import os
import re
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WRAPPER = ROOT / ".teamflow" / "bin" / "teamflow"
AGENTS_DIR = ROOT / ".teamflow" / "agents"


def _parse_frontmatter(text):
    """Parse simple YAML-like frontmatter between ``---`` delimiters."""
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}
    fm = {}
    for line in lines[1:]:
        if line.strip() == "---":
            break
        if ":" in line and not line.startswith((" ", "\t", "#")):
            key, _, value = line.partition(":")
            fm[key.strip()] = value.strip()
    return fm


def _agent_model(role):
    """Return (provider, model) parsed from the agent Markdown frontmatter."""
    text = (AGENTS_DIR / f"{role}.md").read_text(encoding="utf-8")
    fm = _parse_frontmatter(text)
    model_value = fm.get("model", "")
    provider, _, model = model_value.partition("/")
    return provider, model


def _run(args, *, cwd=ROOT, env=None, timeout=30):
    return subprocess.run(
        args,
        cwd=str(cwd),
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
        stdin=subprocess.DEVNULL,
    )


class RoleRegistryTests(unittest.TestCase):
    """Agent identity is resolved from ``.teamflow/agents/<role>.md`` files."""

    def test_core_agent_files_exist(self):
        for role in ("planner", "test-writer", "coder", "command", "test-runner"):
            with self.subTest(role=role):
                agent_path = AGENTS_DIR / f"{role}.md"
                self.assertTrue(
                    agent_path.is_file(),
                    f".teamflow/agents/{role}.md must exist",
                )

    def test_core_agents_have_provider_slash_model(self):
        expected = {
            "planner": ("zhipuai-coding-plan", "glm-5.2"),
            "test-writer": ("zhipuai-coding-plan", "glm-5.2"),
            "coder": ("kimi", "k3"),
            "command": ("mimo", "mimo-v2.5-pro"),
            "test-runner": ("mimo", "mimo-v2.5-pro"),
        }
        for role, (provider, model) in expected.items():
            with self.subTest(role=role):
                actual_provider, actual_model = _agent_model(role)
                self.assertEqual(actual_provider, provider)
                self.assertEqual(actual_model, model)

    def test_test_runner_tools_excludes_edit(self):
        text = (AGENTS_DIR / "test-runner.md").read_text(encoding="utf-8")
        fm = _parse_frontmatter(text)
        tools = fm.get("tools", "")
        self.assertNotIn("edit", tools)

    def test_every_agent_markdown_file_exists_as_system_prompt(self):
        for path in sorted(AGENTS_DIR.glob("*.md")):
            with self.subTest(agent=path.name):
                self.assertTrue(path.is_file())


class RunMappingTests(unittest.TestCase):
    """``--print`` introspection resolves a real ``pi`` argv, offline."""

    def setUp(self):
        self.script = WRAPPER.read_text(encoding="utf-8")

    def _assert_print_mapping(self, args, role, prompt):
        completed = _run(args)
        self.assertEqual(
            completed.returncode, 0,
            f"wrapper --print exited {completed.returncode}: "
            f"stdout={completed.stdout!r} stderr={completed.stderr!r}",
        )
        self.assertTrue(completed.stdout.strip(), "--print must emit JSON on stdout")
        data = json.loads(completed.stdout)
        self.assertEqual(data.get("role"), role)
        env = data.get("env", {})
        self.assertIn("PI_CODING_AGENT_DIR", env)
        self.assertTrue(str(env["PI_CODING_AGENT_DIR"]))
        argv = data.get("argv")
        self.assertIsInstance(argv, list)
        self.assertGreater(len(argv), 1)
        self.assertEqual(argv[0], "pi")
        # Assumption encoded here: the prompt travels as ``pi -p <PROMPT>``.
        self.assertIn("-p", argv)
        self.assertEqual(argv[argv.index("-p") + 1], prompt)
        for token in ("--mode", "json"):
            self.assertIn(token, argv)
        provider, model = _agent_model(role)
        self.assertIn("--provider", argv)
        self.assertEqual(argv[argv.index("--provider") + 1], provider)
        self.assertIn("--model", argv)
        self.assertEqual(argv[argv.index("--model") + 1], model)
        # Assumption encoded here: system prompt is passed as ``--system-prompt <path>``.
        self.assertIn("--system-prompt", argv)
        self.assertTrue(argv[argv.index("--system-prompt") + 1])
        # CRITICAL precision: the resolved pi command must never use ``pi run``
        # or ``--agent`` (pi has neither). Inspect the resolved argv ONLY -- the
        # wrapper script legitimately accepts its own ``--agent`` flag, so we
        # must NOT grep the wrapper source for ``--agent`` here.
        self.assertNotIn("run", argv)
        for token in argv:
            self.assertFalse(
                str(token).startswith("--agent"),
                f"resolved pi argv must not contain an --agent token: {argv}",
            )
        return data

    def test_planner_run_print_resolves_pi_argv(self):
        self._assert_print_mapping(
            [str(WRAPPER), "run", "--agent", "planner", "hello-pi-world", "--print"],
            "planner",
            "hello-pi-world",
        )

    def test_coder_run_print_resolves_kimi_k3(self):
        self._assert_print_mapping(
            [str(WRAPPER), "run", "--agent", "coder", "implement-feature-x", "--print"],
            "coder",
            "implement-feature-x",
        )

    def test_command_print_resolves_mimo(self):
        self._assert_print_mapping(
            [str(WRAPPER), "command", "list-branches", "--print"],
            "command",
            "list-branches",
        )

    def test_wrapper_exports_pi_coding_agent_dir(self):
        self.assertRegex(self.script, r'(?:^|\n)[ \t]*export[ \t]+PI_CODING_AGENT_DIR\b')

    def test_wrapper_does_not_emit_pi_run(self):
        self.assertNotIn("pi run", self.script)

    def test_wrapper_does_not_export_pi_config_dir(self):
        self.assertNotRegex(self.script, r'export[ \t]+PI_CONFIG_DIR\b')


class DebugResourceTests(unittest.TestCase):
    """``debug`` is implemented locally and reads project ``.teamflow/``."""

    def test_debug_agent_planner_reports_project_metadata(self):
        with tempfile.TemporaryDirectory() as home:
            env = os.environ.copy()
            env["HOME"] = home
            env.pop("TEAMFLOW_HOME", None)
            completed = _run([str(WRAPPER), "debug", "agent", "planner"], env=env)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        provider, model = _agent_model("planner")
        self.assertIn("planner", completed.stdout)
        self.assertIn(provider, completed.stdout)
        self.assertIn(model, completed.stdout)

    def test_debug_skill_lists_installed_skills(self):
        with tempfile.TemporaryDirectory() as home:
            env = os.environ.copy()
            env["HOME"] = home
            env.pop("TEAMFLOW_HOME", None)
            completed = _run([str(WRAPPER), "debug", "skill"], env=env)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("plan-change", completed.stdout)
        self.assertIn("basic-memory-cli", completed.stdout)

    def test_debug_agent_nonexistent_fails(self):
        completed = _run([str(WRAPPER), "debug", "agent", "nonexistent-role-xyz"])
        self.assertNotEqual(completed.returncode, 0)


class SessionListTests(unittest.TestCase):
    """``session list --format json`` honors ``TEAMFLOW_PI_SESSION_DIR``."""

    # Assumption encoded here (open question in the handoff): the exact
    # location/schema of real pi session JSONL is abstracted behind
    # TEAMFLOW_PI_SESSION_DIR. The fixture format these tests create is one
    # ``<id>.jsonl`` file per session whose first line is a header record
    # carrying id/model/provider/created_at/updated_at, followed by one JSON
    # record per message. The implementation must adapt real pi session files
    # to the pinned metadata schema when TEAMFLOW_PI_SESSION_DIR points here.
    DISTINCTIVE_BODY = "DISTINCTIVE_PROMPT_BODY_DO_NOT_LEAK_9281"

    def _session_env(self, session_dir):
        env = os.environ.copy()
        env["TEAMFLOW_PI_SESSION_DIR"] = str(session_dir)
        env.pop("TEAMFLOW_HOME", None)
        return env

    def _write_fixture(self, session_dir, session_id="sess-abc", messages=2):
        session_dir.mkdir(parents=True, exist_ok=True)
        lines = [
            json.dumps({
                "type": "session_header",
                "id": session_id,
                "model": "k3",
                "provider": "kimi",
                "created_at": "2026-07-20T10:00:00Z",
                "updated_at": "2026-07-20T10:05:00Z",
            })
        ]
        for i in range(messages):
            lines.append(json.dumps({
                "type": "message",
                "role": "user",
                "content": f"{self.DISTINCTIVE_BODY}-{i}",
            }))
        path = session_dir / f"{session_id}.jsonl"
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return path

    def test_empty_session_dir_returns_empty_array(self):
        with tempfile.TemporaryDirectory() as session_dir:
            completed = _run(
                [str(WRAPPER), "session", "list", "--format", "json"],
                cwd=ROOT,
                env=self._session_env(Path(session_dir)),
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertEqual(json.loads(completed.stdout), [])

    def test_fixture_session_yields_metadata_only(self):
        with tempfile.TemporaryDirectory() as session_dir:
            self._write_fixture(Path(session_dir))
            completed = _run(
                [str(WRAPPER), "session", "list", "--format", "json"],
                cwd=ROOT,
                env=self._session_env(Path(session_dir)),
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            data = json.loads(completed.stdout)
            self.assertIsInstance(data, list)
            self.assertEqual(len(data), 1)
            entry = data[0]
            for field in ("id", "model", "provider", "created_at", "updated_at", "message_count"):
                with self.subTest(field=field):
                    self.assertIn(field, entry)
            self.assertEqual(entry["model"], "k3")
            self.assertEqual(entry["provider"], "kimi")
            self.assertEqual(entry["message_count"], 2)
            # Metadata-only: the raw message body must not leak into the listing.
            self.assertNotIn(self.DISTINCTIVE_BODY, completed.stdout)

    def test_limit_caps_number_of_sessions(self):
        with tempfile.TemporaryDirectory() as session_dir:
            base = Path(session_dir)
            self._write_fixture(base, "sess-one")
            self._write_fixture(base, "sess-two")
            for flag in ("--limit", "-n"):
                with self.subTest(flag=flag):
                    completed = _run(
                        [str(WRAPPER), "session", "list", "--format", "json", flag, "1"],
                        cwd=ROOT,
                        env=self._session_env(base),
                    )
                    self.assertEqual(completed.returncode, 0, completed.stderr)
                    data = json.loads(completed.stdout)
                    self.assertIsInstance(data, list)
                    self.assertLessEqual(len(data), 1)

    def test_human_format_without_json_still_succeeds(self):
        with tempfile.TemporaryDirectory() as session_dir:
            self._write_fixture(Path(session_dir))
            completed = _run(
                [str(WRAPPER), "session", "list"],
                cwd=ROOT,
                env=self._session_env(Path(session_dir)),
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)


class RealPiSmokeTests(unittest.TestCase):
    """Smoke-checks the REAL installed ``pi`` binary surface (offline)."""

    def test_pi_version_is_present_and_pinned(self):
        completed = subprocess.run(
            ["pi", "--version"], capture_output=True, text=True, stdin=subprocess.DEVNULL
        )
        self.assertEqual(completed.returncode, 0, "pi --version must succeed")
        self.assertTrue(
            completed.stdout.startswith("0.82.1"),
            f"pi version must start with 0.82.1, got: {completed.stdout!r}",
        )

    def test_pi_help_exposes_required_flags(self):
        completed = subprocess.run(
            ["pi", "--help"], capture_output=True, text=True, stdin=subprocess.DEVNULL
        )
        help_text = completed.stdout + completed.stderr
        for flag in (
            "-p", "--mode", "--provider", "--model", "--system-prompt",
            "--extension", "--skill", "--tools", "--session-id",
        ):
            with self.subTest(flag=flag):
                self.assertIn(flag, help_text)

    def test_pi_help_has_no_agent_flag_or_run_subcommand(self):
        completed = subprocess.run(
            ["pi", "--help"], capture_output=True, text=True, stdin=subprocess.DEVNULL
        )
        help_text = completed.stdout + completed.stderr
        self.assertNotIn("--agent", help_text)
        # Robust check for an absent ``run`` subcommand. The strongest signal
        # is that ``--agent`` is not a flag (pi has no role layer); we
        # additionally require no top-level ``run`` command listing in --help.
        self.assertIsNone(re.search(r'(?m)^[ \t]*run[ \t]', help_text))


class PiConfigCredentialTests(unittest.TestCase):
    """Config files use env interpolation; models.json is sole provider config."""

    SECRET_TOKEN_RE = re.compile(r'(?:[A-Za-z0-9+/]{40,}={0,2}|[0-9a-fA-F]{40,})')
    SECRET_KEY_RE = re.compile(r'(?i)(api[_-]?key|token|secret|password)')
    EXCLUDED_NAMES = {"manifest.json", "package.json", "package-lock.json"}

    def _iter_config_jsons(self):
        for path in sorted((ROOT / ".teamflow").glob("*.json")):
            if path.name in self.EXCLUDED_NAMES:
                continue
            yield path

    def test_no_literal_secret_values_in_teamflow_json_files(self):
        offenders = []
        for path in self._iter_config_jsons():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                self.fail(f"{path} must be valid JSON")
            stack = [data]
            while stack:
                node = stack.pop()
                if isinstance(node, dict):
                    stack.extend(node.values())
                elif isinstance(node, list):
                    stack.extend(node)
                elif isinstance(node, str):
                    if self.SECRET_TOKEN_RE.search(node):
                        offenders.append((path.name, node[:24]))
        self.assertEqual(offenders, [], f"literal secret-looking tokens found: {offenders}")

    def test_credential_fields_use_env_interpolation(self):
        offenders = []
        for path in self._iter_config_jsons():
            data = json.loads(path.read_text(encoding="utf-8"))
            stack = [(data, None)]
            while stack:
                node, key = stack.pop()
                if isinstance(node, dict):
                    for k, v in node.items():
                        stack.append((v, k))
                elif isinstance(node, list):
                    for v in node:
                        stack.append((v, key))
                elif isinstance(node, str) and key is not None and self.SECRET_KEY_RE.search(key):
                    ok = node == "" or node.startswith("$") or "{env:" in node
                    if not ok:
                        offenders.append((path.name, key, node[:24]))
        self.assertEqual(offenders, [], f"credential fields must use env interpolation: {offenders}")


class InstallerFootprintTests(unittest.TestCase):
    """The installer ships the runtime files below ``.teamflow/`` only."""

    def setUp(self):
        self.init_script = (ROOT / "scripts/install.sh").read_text(encoding="utf-8")

    def test_files_list_includes_new_runtime_artifacts(self):
        match = re.search(r'\nFILES=\(\n(.*?)\n\)', self.init_script, re.DOTALL)
        self.assertIsNotNone(match, "scripts/install must define a FILES=(...) block")
        block = match.group(1)
        self.assertIn(".teamflow/bin/pi-runtime", block)

    def test_hermetic_install_copies_new_files_and_debug_works(self):
        with tempfile.TemporaryDirectory() as directory:
            tools = _IsolatedTools(Path(directory))
            project = tools.new_git_project("target")
            completed = tools.initialize(project)
            self.assertEqual(completed.returncode, 0, completed.stderr)

            runtime_installed = project / ".teamflow" / "bin" / "pi-runtime"
            self.assertTrue(runtime_installed.is_file(), ".teamflow/bin/pi-runtime must be installed")

            manifest = json.loads(
                (project / ".teamflow" / "manifest.json").read_text(encoding="utf-8")
            )
            files = manifest.get("files")
            self.assertTrue(files, "manifest must list installed files")
            keys = list(files.keys()) if isinstance(files, dict) else list(files)
            for key in keys:
                with self.subTest(key=key):
                    self.assertTrue(
                        key.startswith(".teamflow/"),
                        f"installer must write only below .teamflow/: {key}",
                    )
            self.assertIn(".teamflow/bin/pi-runtime", keys)

            debug_skill = subprocess.run(
                ["./.teamflow/bin/teamflow", "debug", "skill"],
                cwd=str(project),
                env=tools.debug_env(),
                capture_output=True,
                text=True,
                stdin=subprocess.DEVNULL,
                timeout=30,
            )
            self.assertEqual(debug_skill.returncode, 0, debug_skill.stderr)
            self.assertIn("plan-change", debug_skill.stdout)
            self.assertIn("basic-memory-cli", debug_skill.stdout)

            debug_agent = subprocess.run(
                ["./.teamflow/bin/teamflow", "debug", "agent", "planner"],
                cwd=str(project),
                env=tools.debug_env(),
                capture_output=True,
                text=True,
                stdin=subprocess.DEVNULL,
                timeout=30,
            )
            self.assertEqual(debug_agent.returncode, 0, debug_agent.stderr)


class _IsolatedTools:
    """Hermetic installer fixtures mirroring tests/test_teamflow_namespace.py."""

    def __init__(self, root):
        self.root = root
        self.home = root / "home"
        self.bin = root / "fake-bin"
        self.home.mkdir(parents=True)
        self.bin.mkdir(parents=True)
        self._write_executable(
            self.bin / "pi",
            "#!/bin/sh\n"
            'if [ "${1:-}" = "--version" ]; then\n'
            "  printf '0.82.1\\n'\n"
            'elif [ "${1:-}" = "debug" ] && [ "${2:-}" = "skill" ]; then\n'
            "  printf 'plan-change\\nbasic-memory-cli\\n'\n"
            "fi\n"
            "exit 0\n",
        )
        self._write_executable(
            self.bin / "basic-memory",
            "#!/bin/sh\n"
            'if [ "${1:-} ${2:-}" = "project info" ]; then\n'
            "  exit 1\n"
            "fi\n"
            'if [ "${1:-}" = "status" ]; then\n'
            "  printf '{}\\n'\n"
            "fi\n"
            "exit 0\n",
        )

    def _write_executable(self, path, text):
        path.write_text(text, encoding="utf-8")
        path.chmod(0o755)

    def new_git_project(self, name):
        project = self.root / name
        project.mkdir()
        subprocess.run(["git", "init", "-q", str(project)], check=True)
        return project

    def _base_env(self):
        env = os.environ.copy()
        for key in list(env):
            if key.startswith(("TEAMFLOW_", "WORKFLOW_", "OPENCODE_WORKFLOW_")):
                env.pop(key)
        env.update({
            "HOME": str(self.home),
            "PATH": f"{self.bin}:{env['PATH']}",
        })
        return env

    def initialize(self, project):
        return subprocess.run(
            [str(ROOT / "scripts/install.sh"), str(project)],
            cwd=ROOT,
            env=self._base_env(),
            text=True,
            capture_output=True,
            timeout=60,
        )

    def debug_env(self):
        return self._base_env()


if __name__ == "__main__":
    unittest.main()
