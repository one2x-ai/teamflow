"""Requirement tests for the Pi inner-loop runtime migration.

These tests assert the invariants of run-id ``pi-inner-loop-runtime``:
OpenCode is removed from every active runtime path and
``@earendil-works/pi-coding-agent`` is the installed, checked, and
configured runtime. Provider and credential policy is verified through
``.teamflow/models.json`` (the sole provider config) rather than the
deleted ``config.json``.
"""

import json
import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODELS_PATH = ROOT / ".teamflow" / "models.json"


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


class PiRuntimeRoutingTests(unittest.TestCase):
    """F1: the teamflow launcher has no OpenCode in active runtime paths."""

    def setUp(self):
        self.script = (ROOT / ".teamflow/bin/teamflow").read_text(encoding="utf-8")

    def test_no_opencode_environment_is_exported(self):
        self.assertNotIn("export OPENCODE_CONFIG", self.script)
        self.assertNotIn("OPENCODE_CONFIG_DIR", self.script)

    def test_no_opencode_run_or_agent_invocation_remains(self):
        self.assertNotIn("opencode run", self.script)
        self.assertNotIn("opencode --agent", self.script)

    def test_no_exec_statement_targets_opencode(self):
        for line in self.script.splitlines():
            with self.subTest(line=line.strip()):
                self.assertNotRegex(line, r'^\s*exec\s+opencode\b')

    def test_default_and_command_exec_target_pi(self):
        exec_lines = [
            line for line in self.script.splitlines()
            if re.match(r'\s*exec\b', line)
        ]
        self.assertTrue(exec_lines, "teamflow launcher must exec a runtime")
        self.assertTrue(
            any("pi" in line for line in exec_lines),
            "the runtime exec target must be pi",
        )
        self.assertFalse(
            any("opencode" in line for line in exec_lines),
            "no exec statement may target opencode",
        )

    def test_command_subcommand_branch_is_present(self):
        self.assertIn('== "command"', self.script)

    def test_local_subcommand_dispatch_is_preserved(self):
        for sub in (
            "memory",
            "memory-capture",
            "test-patch",
            "server",
            "source-check",
            "phase",
        ):
            with self.subTest(subcommand=sub):
                self.assertIn(f'== "{sub}"', self.script)


class PiProviderPolicyTests(unittest.TestCase):
    """F2: provider/role model policy is preserved under Pi via models.json."""

    def setUp(self):
        self.models_text = MODELS_PATH.read_text(encoding="utf-8")
        self.models = json.loads(self.models_text)

    def test_four_providers_are_configured_in_models_json(self):
        providers = self.models.get("providers", {})
        for name in ("zhipuai-coding-plan", "deepseek", "kimi", "mimo"):
            with self.subTest(provider=name):
                self.assertIn(name, providers)

    def test_credentials_use_env_interpolation_only(self):
        providers = self.models.get("providers", {})
        offenders = []
        for provider_id, provider in providers.items():
            if not isinstance(provider, dict):
                continue
            api_key = provider.get("apiKey")
            if api_key is None:
                continue
            if not (
                isinstance(api_key, str)
                and (api_key == "" or api_key.startswith("$"))
            ):
                offenders.append((provider_id, api_key))
        self.assertEqual(
            offenders, [],
            "apiKey values must be empty or Pi env-interpolated ($VAR / ${VAR})",
        )

    def test_agent_model_tokens_persist(self):
        expected = {
            "planner.md": "glm-5.2",
            "test-writer.md": "glm-5.2",
            "coder.md": "k3",
            "test-runner.md": "mimo-v2.5-pro",
            "command.md": "mimo-v2.5-pro",
            "emotional-salience-sensor.md": "mimo-v2.5-pro",
            "memory-compressor.md": "deepseek-v4-pro",
            "memory-extractor.md": "glm-5.2",
            "memory-formatter.md": "glm-5.2",
        }
        for name, token in expected.items():
            with self.subTest(agent=name):
                text = (ROOT / ".teamflow/agents" / name).read_text(encoding="utf-8")
                self.assertIn(token, text)

    def test_test_runner_tools_excludes_edit(self):
        text = (ROOT / ".teamflow/agents/test-runner.md").read_text(encoding="utf-8")
        fm = _parse_frontmatter(text)
        tools = fm.get("tools", "")
        self.assertNotIn("edit", tools)


class PiBootstrapAndDoctorTests(unittest.TestCase):
    """F4: bootstrap installs Pi and requires node >=22.19.0; doctor checks Pi."""

    def test_bootstrap_installs_pi_agent_not_opencode(self):
        bootstrap = (ROOT / "scripts/bootstrap.sh").read_text(encoding="utf-8")
        self.assertIn("@earendil-works/pi-coding-agent", bootstrap)
        self.assertNotIn("opencode-ai", bootstrap)
        self.assertNotIn("npm install --global opencode-ai", bootstrap)

    def test_bootstrap_requires_node_22_19_or_newer(self):
        bootstrap = (ROOT / "scripts/bootstrap.sh").read_text(encoding="utf-8")
        self.assertIn("22.19.0", bootstrap)

    def test_bootstrap_preserves_teamflow_launcher_wiring(self):
        bootstrap = (ROOT / "scripts/bootstrap.sh").read_text(encoding="utf-8")
        self.assertRegex(bootstrap, r'LAUNCHER_PATH="\$LAUNCHER_DIR/teamflow"')
        self.assertIn("scripts/teamflow", bootstrap)

    def test_doctor_runtime_check_targets_pi_with_minimum(self):
        doctor = (ROOT / "scripts/doctor.sh").read_text(encoding="utf-8")
        self.assertNotIn("MIN_OPENCODE_VERSION", doctor)
        self.assertNotIn("command -v opencode", doctor)
        self.assertRegex(doctor, r'command -v\s+"?pi"?')
        self.assertRegex(doctor, r'MIN_[A-Z_]*PI[A-Z_]*\s*=')


class PiInitProjectTests(unittest.TestCase):
    """F5: init-project command gate lists pi; footprint stays under .teamflow/."""

    def test_command_presence_gate_lists_pi_not_opencode(self):
        initializer = (ROOT / "scripts/install.sh").read_text(encoding="utf-8")
        match = re.search(r'for command_name in ([^\n]+)', initializer)
        self.assertIsNotNone(match, "command-presence gate must exist")
        gate = match.group(1)
        self.assertIn("node", gate)
        self.assertIn("basic-memory", gate)
        self.assertIn("pi", gate)
        self.assertNotIn("opencode", gate)

    def test_install_footprint_remains_under_teamflow(self):
        initializer = (ROOT / "scripts/install.sh").read_text(encoding="utf-8")
        self.assertIn(".teamflow/", initializer)


if __name__ == "__main__":
    unittest.main()
