"""Requirement tests for run-id ``pi-agent-native-cleanup-20260727``.

These tests pin the Pi-native agent identity migration:
  - roles.json and config.json are deleted;
  - agent identity is resolved from Markdown frontmatter only;
  - models.json is the sole provider config with 4 providers;
  - the teamflow-task extension gains a task_group tool with bounded
    concurrency alongside the existing synchronous task tool;
  - both tools share the same depth-0 planner gate.

All paths are relative to the repository root
``ROOT = Path(__file__).resolve().parents[1]``.
"""

import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODELS_PATH = ROOT / ".teamflow" / "models.json"
PI_RUNTIME = ROOT / ".teamflow" / "bin" / "pi-runtime"
WRAPPER = ROOT / ".teamflow" / "bin" / "teamflow"
EXTENSION_FILE = ROOT / ".teamflow" / "extensions" / "teamflow-task" / "index.ts"
AGENTS_DIR = ROOT / ".teamflow" / "agents"
INIT_SCRIPT = ROOT / "scripts" / "init-project.sh"
DOCTOR_SCRIPT = ROOT / "scripts" / "doctor.sh"
README = ROOT / "README.md"


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


# ===== AC1: roles.json and config.json deleted; models.json is sole config =====

class DeletedFilesTests(unittest.TestCase):
    """AC1a: transitional compatibility files must not exist."""

    def test_roles_json_is_deleted(self):
        self.assertFalse(
            (ROOT / ".teamflow" / "roles.json").exists(),
            ".teamflow/roles.json must be deleted",
        )

    def test_config_json_is_deleted(self):
        self.assertFalse(
            (ROOT / ".teamflow" / "config.json").exists(),
            ".teamflow/config.json must be deleted",
        )


class ModelsJsonSoleConfigTests(unittest.TestCase):
    """AC1b: models.json exists and has all 4 providers."""

    def test_models_json_has_all_four_providers(self):
        self.assertTrue(MODELS_PATH.is_file(), ".teamflow/models.json must exist")
        data = json.loads(MODELS_PATH.read_text(encoding="utf-8"))
        providers = data.get("providers", {})
        for name in ("zhipuai-coding-plan", "deepseek", "kimi", "mimo"):
            with self.subTest(provider=name):
                self.assertIn(name, providers)


class PiRuntimeNoLegacyReferencesTests(unittest.TestCase):
    """AC1c: pi-runtime source must not contain legacy roles.json references."""

    def setUp(self):
        self.text = PI_RUNTIME.read_text(encoding="utf-8")

    def test_no_roles_json_reference(self):
        self.assertNotIn("roles.json", self.text)

    def test_no_roles_path_constant(self):
        self.assertNotIn("ROLES_PATH", self.text)

    def test_no_load_roles_function(self):
        self.assertNotIn("_load_roles", self.text)


class InitScriptCurrentFootprintTests(unittest.TestCase):
    """AC1d: the installer contains only the current managed footprint."""

    def setUp(self):
        self.script = INIT_SCRIPT.read_text(encoding="utf-8")

    def test_files_list_excludes_config_json(self):
        match = re.search(r'\nFILES=\(\n(.*?)\n\)', self.script, re.DOTALL)
        self.assertIsNotNone(match, "FILES block must exist")
        self.assertNotIn(".teamflow/config.json", match.group(1))

    def test_files_list_excludes_roles_json(self):
        match = re.search(r'\nFILES=\(\n(.*?)\n\)', self.script, re.DOTALL)
        self.assertIsNotNone(match, "FILES block must exist")
        self.assertNotIn(".teamflow/roles.json", match.group(1))

    def test_no_legacy_files_block(self):
        self.assertNotIn("LEGACY_FILES", self.script)


class DoctorScriptNoConfigCheckTests(unittest.TestCase):
    """AC1e: doctor.sh does not validate .teamflow/config.json."""

    def test_doctor_does_not_reference_teamflow_config_json(self):
        text = DOCTOR_SCRIPT.read_text(encoding="utf-8")
        self.assertNotIn(".teamflow/config.json", text)


class ReadmeNoLegacyReferencesTests(unittest.TestCase):
    """AC1f: README.md does not reference roles.json or config.json."""

    def test_readme_no_roles_json(self):
        self.assertNotIn("roles.json", README.read_text(encoding="utf-8"))

    def test_readme_no_config_json(self):
        self.assertNotIn("config.json", README.read_text(encoding="utf-8"))


# ===== AC2: Agent frontmatter has only description, model, optional tools =====

class AgentFrontmatterTests(unittest.TestCase):
    """AC2: every agent .md frontmatter has only the allowed keys."""

    ALLOWED_KEYS = {"description", "model", "tools", "delegates"}
    FORBIDDEN_KEYS = {"mode", "permission", "temperature", "steps"}

    def _agent_files(self):
        return sorted(AGENTS_DIR.glob("*.md"))

    def test_at_least_one_agent_file(self):
        self.assertGreater(len(self._agent_files()), 0)

    def test_frontmatter_has_only_allowed_keys(self):
        for path in self._agent_files():
            with self.subTest(agent=path.name):
                fm = _parse_frontmatter(path.read_text(encoding="utf-8"))
                for key in fm:
                    self.assertIn(
                        key, self.ALLOWED_KEYS,
                        f"{path.name}: frontmatter key '{key}' is not allowed",
                    )

    def test_frontmatter_has_no_forbidden_keys(self):
        for path in self._agent_files():
            with self.subTest(agent=path.name):
                fm = _parse_frontmatter(path.read_text(encoding="utf-8"))
                for key in self.FORBIDDEN_KEYS:
                    self.assertNotIn(key, fm)

    def test_every_agent_has_model_with_provider_slash_model(self):
        for path in self._agent_files():
            with self.subTest(agent=path.name):
                fm = _parse_frontmatter(path.read_text(encoding="utf-8"))
                model = fm.get("model", "")
                self.assertIn("/", model, f"{path.name}: model must contain '/'")
                provider, _, model_name = model.partition("/")
                self.assertTrue(provider.strip(), f"{path.name}: provider part is empty")
                self.assertTrue(model_name.strip(), f"{path.name}: model part is empty")

    def test_every_agent_has_nonempty_description(self):
        for path in self._agent_files():
            with self.subTest(agent=path.name):
                fm = _parse_frontmatter(path.read_text(encoding="utf-8"))
                desc = fm.get("description", "")
                self.assertTrue(
                    desc.strip(),
                    f"{path.name}: description must be non-empty",
                )

    def test_tools_if_present_is_comma_separated_string(self):
        for path in self._agent_files():
            with self.subTest(agent=path.name):
                fm = _parse_frontmatter(path.read_text(encoding="utf-8"))
                if "tools" not in fm:
                    continue
                tools_value = fm["tools"]
                self.assertIsInstance(tools_value, str)
                self.assertNotIn("{", tools_value)
                self.assertNotIn("[", tools_value)


# ===== AC3: pi-runtime resolves from Markdown only =====

class PiRuntimeMarkdownResolutionTests(unittest.TestCase):
    """AC3: provider/model/system-prompt resolved from agent Markdown."""

    def _run_print(self, role, prompt):
        with tempfile.TemporaryDirectory() as home:
            env = os.environ.copy()
            env["HOME"] = home
            for key in list(env):
                if key.startswith(("TEAMFLOW_", "WORKFLOW_", "OPENCODE_WORKFLOW_")):
                    env.pop(key)
            completed = _run(
                [str(WRAPPER), "run", "--agent", role, prompt, "--print"],
                env=env,
            )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertTrue(completed.stdout.strip(), "--print must emit JSON")
        return json.loads(completed.stdout)

    def test_planner_print_resolves_zhipu_glm(self):
        data = self._run_print("planner", "ping")
        argv = data["argv"]
        self.assertIn("--provider", argv)
        self.assertEqual(argv[argv.index("--provider") + 1], "zhipuai-coding-plan")
        self.assertIn("--model", argv)
        self.assertEqual(argv[argv.index("--model") + 1], "glm-5.2")
        self.assertIn("--system-prompt", argv)
        self.assertIn("--extension", argv)

    def test_coder_print_resolves_kimi_k3(self):
        data = self._run_print("coder", "do-work")
        argv = data["argv"]
        self.assertIn("--provider", argv)
        self.assertEqual(argv[argv.index("--provider") + 1], "kimi")
        self.assertIn("--model", argv)
        self.assertEqual(argv[argv.index("--model") + 1], "k3")

    def test_system_prompt_path_points_to_markdown(self):
        data = self._run_print("planner", "ping")
        argv = data["argv"]
        sp = argv[argv.index("--system-prompt") + 1]
        self.assertIn("agents/planner.md", sp)


class DebugAgentMarkdownResolutionTests(unittest.TestCase):
    """AC3: debug agent resolves identity from Markdown."""

    def test_debug_agent_planner_shows_markdown_identity(self):
        with tempfile.TemporaryDirectory() as home:
            env = os.environ.copy()
            env["HOME"] = home
            for key in list(env):
                if key.startswith(("TEAMFLOW_", "WORKFLOW_", "OPENCODE_WORKFLOW_")):
                    env.pop(key)
            completed = _run(
                [str(WRAPPER), "debug", "agent", "planner"],
                env=env,
            )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("planner", completed.stdout)
        self.assertIn("zhipuai-coding-plan", completed.stdout)
        self.assertIn("glm-5.2", completed.stdout)

    def test_debug_agent_nonexistent_fails(self):
        completed = _run(
            [str(WRAPPER), "debug", "agent", "nonexistent-role-xyz"],
        )
        self.assertNotEqual(completed.returncode, 0)


# ===== AC4: task remains synchronous single-child =====

class TaskToolContractTests(unittest.TestCase):
    """AC4: the extension still registers the synchronous task tool."""

    def setUp(self):
        self.text = EXTENSION_FILE.read_text(encoding="utf-8")

    def test_registers_task_tool(self):
        self.assertRegex(self.text, r"['\"]task['\"]")

    def test_task_has_agent_parameter(self):
        self.assertIn("agent", self.text)

    def test_task_has_prompt_parameter(self):
        self.assertIn("prompt", self.text)


# ===== AC5: task_group with bounded concurrency =====

class TaskGroupToolTests(unittest.TestCase):
    """AC5: the extension registers task_group with bounded concurrency."""

    def setUp(self):
        self.text = EXTENSION_FILE.read_text(encoding="utf-8")

    def test_registers_task_group_tool(self):
        self.assertRegex(self.text, r"['\"]task_group['\"]")

    def test_has_tasks_parameter(self):
        self.assertIn("tasks", self.text)

    def test_has_max_concurrency_parameter(self):
        self.assertIn("max_concurrency", self.text)

    def test_has_concurrency_limiting_logic(self):
        self.assertTrue(
            re.search(
                r"max_concurrency|worker|Promise\.all|semaphore|activeCount|running",
                self.text,
                re.IGNORECASE,
            ),
            "task_group must contain concurrency-limiting logic",
        )

    def test_preserves_input_order_in_results(self):
        self.assertTrue(
            re.search(
                r"results\s*\[\s*\w+\s*\]|inputOrder|ordered|index",
                self.text,
                re.IGNORECASE,
            ),
            "task_group must preserve input order in results",
        )

    def test_reports_per_task_success_or_failure(self):
        self.assertTrue(
            re.search(r"success|exitCode|error|failed|status", self.text, re.IGNORECASE),
            "task_group must report per-task success/failure",
        )

    def test_propagates_cancellation(self):
        self.assertTrue(
            "signal" in self.text.lower() or "abort" in self.text.lower(),
            "task_group must propagate cancellation (signal/abort)",
        )

    def test_has_orphan_prevention(self):
        self.assertTrue(
            re.search(
                r"finally|cleanup|kill|destroy|close|terminate",
                self.text,
                re.IGNORECASE,
            ),
            "task_group must have orphan prevention (cleanup/kill/finally)",
        )


# ===== AC6: depth gating for both tools =====

class DepthGatingTests(unittest.TestCase):
    """AC6: both tools require explicit delegation permission at depth 0."""

    def setUp(self):
        self.text = EXTENSION_FILE.read_text(encoding="utf-8")

    def test_both_tools_inside_depth_gate(self):
        self.assertIn("TEAMFLOW_AGENT_DEPTH", self.text)
        self.assertIn("frontmatter.delegates", self.text)
        self.assertRegex(self.text, r"['\"]task['\"]")
        self.assertRegex(self.text, r"['\"]task_group['\"]")
        self.assertTrue(
            re.search(r"===\s*0|==\s*0|<\s*1|!==\s*0", self.text),
            "depth must be compared to 0",
        )


# ===== Delegates-based delegation gate =====

class DelegatesGateTests(unittest.TestCase):
    """Generalized delegation gate: any depth-0 role with delegates: true."""

    def setUp(self):
        self.text = EXTENSION_FILE.read_text(encoding="utf-8")

    def test_extension_reads_delegates_from_frontmatter(self):
        self.assertIn(
            "delegates", self.text,
            "extension must read 'delegates' from role frontmatter",
        )

    def test_extension_no_hardcoded_planner_role_gate(self):
        self.assertNotRegex(
            self.text,
            r'role\s*(?:===|!==)\s*["\']planner["\']',
            "gate must not hardcode role === 'planner' or role !== 'planner'",
        )

    def test_extension_checks_strict_boolean_true(self):
        self.assertRegex(
            self.text,
            r'frontmatter\.delegates\s*===\s*true',
            "gate must check frontmatter.delegates === true (strict boolean equality)",
        )

    def test_extension_still_requires_depth_zero(self):
        self.assertIn("TEAMFLOW_AGENT_DEPTH", self.text)
        self.assertRegex(
            self.text,
            r'===\s*0|!==\s*0',
            "gate must still compare depth to 0",
        )

    def test_planner_has_delegates_true(self):
        fm = _parse_frontmatter(
            (AGENTS_DIR / "planner.md").read_text(encoding="utf-8")
        )
        self.assertIn("delegates", fm, "planner.md must have delegates key")
        self.assertEqual(
            fm["delegates"], "true",
            "planner.md delegates must be 'true'",
        )

    def test_no_other_agent_has_delegates_true(self):
        for path in sorted(AGENTS_DIR.glob("*.md")):
            if path.name == "planner.md":
                continue
            with self.subTest(agent=path.name):
                fm = _parse_frontmatter(path.read_text(encoding="utf-8"))
                self.assertNotEqual(
                    fm.get("delegates"), "true",
                    f"{path.name} must not have delegates: true",
                )


# ===== Part C: provider-free real Pi extension load test =====

class PiExtensionLoadTest(unittest.TestCase):
    """Provider-free load: pi --extension <path> --help exits 0."""

    def test_pi_extension_loads_without_provider(self):
        if shutil.which("pi") is None:
            self.skipTest("pi binary not installed on PATH")
        env = os.environ.copy()
        env["TEAMFLOW_AGENT_ROLE"] = "planner"
        env["TEAMFLOW_AGENT_DEPTH"] = "0"
        completed = subprocess.run(
            ["pi", "--extension", str(EXTENSION_FILE), "--help"],
            cwd=str(ROOT),
            env=env,
            capture_output=True,
            text=True,
            stdin=subprocess.DEVNULL,
            timeout=30,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)


if __name__ == "__main__":
    unittest.main()
