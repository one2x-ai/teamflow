"""Requirement tests for run-id ``pi-models-config-20260726``.

Pi 0.82.1 resolves providers/models from ``<$PI_CODING_AGENT_DIR>/models.json``.
The ``teamflow`` wrapper exports ``PI_CODING_AGENT_DIR=$ROOT/.teamflow``, so Pi
reads ``.teamflow/models.json`` (NOT ``.teamflow/config.json``). These tests pin
the Pi-native provider registry that the installer must ship.

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
AGENTS_DIR = ROOT / ".teamflow" / "agents"
INIT_SCRIPT = ROOT / "scripts" / "install.sh"

# Mirror tests/test_pi_wrapper.py: a long base64-ish or hex run is treated as a
# literal secret token and must never appear in models.json.
SECRET_TOKEN_RE = re.compile(r'(?:[A-Za-z0-9+/]{40,}={0,2}|[0-9a-fA-F]{40,})')


def _load_models():
    return json.loads(MODELS_PATH.read_text(encoding="utf-8"))


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


def _walk_strings(node):
    """Yield every string value anywhere in a JSON tree."""
    stack = [node]
    while stack:
        current = stack.pop()
        if isinstance(current, dict):
            stack.extend(current.values())
        elif isinstance(current, list):
            stack.extend(current)
        elif isinstance(current, str):
            yield current


class ModelsJsonSchemaTests(unittest.TestCase):
    """AC 1, 3, 4: models.json exists, is valid JSON, and is secret-free."""

    def test_models_json_exists(self):
        self.assertTrue(
            MODELS_PATH.is_file(),
            ".teamflow/models.json must exist so Pi can resolve providers",
        )

    def test_models_json_is_valid_json_with_providers_object(self):
        self.assertTrue(MODELS_PATH.is_file(), ".teamflow/models.json must exist")
        data = _load_models()
        self.assertIsInstance(data, dict, "models.json top level must be an object")
        providers = data.get("providers")
        self.assertIsInstance(
            providers, dict, "models.json must have a top-level 'providers' object"
        )

    def test_every_custom_provider_has_required_fields(self):
        self.assertTrue(MODELS_PATH.is_file(), ".teamflow/models.json must exist")
        providers = _load_models().get("providers", {})
        self.assertGreaterEqual(
            len(providers), 1, "at least one provider must be configured"
        )
        for provider_id, provider in providers.items():
            with self.subTest(provider=provider_id):
                self.assertIsInstance(provider, dict)
                base_url = provider.get("baseUrl")
                self.assertIsInstance(base_url, str)
                self.assertTrue(
                    base_url.strip(),
                    f"provider {provider_id} must have a non-empty baseUrl",
                )
                self.assertEqual(
                    provider.get("api"),
                    "openai-completions",
                    f"provider {provider_id} api must be 'openai-completions'",
                )
                models = provider.get("models")
                self.assertIsInstance(
                    models, list, f"provider {provider_id} must have a models array"
                )
                self.assertGreaterEqual(
                    len(models), 1, f"provider {provider_id} must list at least one model"
                )
                for entry in models:
                    self.assertIsInstance(entry, dict)
                    model_id = entry.get("id")
                    self.assertIsInstance(model_id, str)
                    self.assertTrue(
                        model_id.strip(),
                        f"provider {provider_id} has a model with an empty id",
                    )

    def test_api_keys_use_pi_env_interpolation_only(self):
        self.assertTrue(MODELS_PATH.is_file(), ".teamflow/models.json must exist")
        providers = _load_models().get("providers", {})
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

    def test_no_literal_secret_tokens_anywhere(self):
        self.assertTrue(MODELS_PATH.is_file(), ".teamflow/models.json must exist")
        offenders = [
            s[:24] for s in _walk_strings(_load_models()) if SECRET_TOKEN_RE.search(s)
        ]
        self.assertEqual(
            offenders, [], "literal secret-looking tokens found in models.json"
        )

    def test_no_opencode_env_syntax_anywhere(self):
        self.assertTrue(MODELS_PATH.is_file(), ".teamflow/models.json must exist")
        offenders = [s[:24] for s in _walk_strings(_load_models()) if "{env:" in s]
        self.assertEqual(
            offenders, [],
            "OpenCode {env:...} syntax must not appear in models.json; Pi uses $VAR/${VAR}",
        )


class RoleProviderModelCoverageTests(unittest.TestCase):
    """AC 2: every agent Markdown model resolves through models.json."""

    # Independent floor so coverage does not silently collapse: the four
    # providers backing the active inner-loop and memory roles.
    MINIMUM_COVERAGE = {
        "zhipuai-coding-plan": "glm-5.2",
        "deepseek": "deepseek-v4-pro",
        "kimi": "k3",
        "mimo": "mimo-v2.5-pro",
    }

    def test_every_agent_provider_and_model_resolves(self):
        self.assertTrue(MODELS_PATH.is_file(), ".teamflow/models.json must exist")
        providers = _load_models().get("providers", {})
        for agent_path in sorted(AGENTS_DIR.glob("*.md")):
            with self.subTest(agent=agent_path.name):
                fm = _parse_frontmatter(agent_path.read_text(encoding="utf-8"))
                model_value = fm.get("model", "")
                self.assertIn("/", model_value, f"{agent_path.name}: model must contain '/'")
                provider_id, _, model_id = model_value.partition("/")
                provider_id = provider_id.strip()
                model_id = model_id.strip()
                self.assertIn(
                    provider_id, providers,
                    f"agent {agent_path.name} provider {provider_id!r} missing from models.json",
                )
                models = providers[provider_id].get("models")
                self.assertIsInstance(
                    models, list, f"provider {provider_id} must have a models array"
                )
                ids = [m.get("id") for m in models if isinstance(m, dict)]
                self.assertIn(
                    model_id, ids,
                    f"agent {agent_path.name} model {model_id!r} missing from provider {provider_id!r}",
                )

    def test_minimum_inner_loop_coverage_present(self):
        self.assertTrue(MODELS_PATH.is_file(), ".teamflow/models.json must exist")
        providers = _load_models().get("providers", {})
        for provider_id, model_id in self.MINIMUM_COVERAGE.items():
            with self.subTest(provider=provider_id, model=model_id):
                self.assertIn(provider_id, providers)
                models = providers[provider_id].get("models")
                self.assertIsInstance(models, list)
                ids = [m.get("id") for m in models if isinstance(m, dict)]
                self.assertIn(model_id, ids)


class InitProjectFilesListTests(unittest.TestCase):
    """AC 5: the installer ships .teamflow/models.json."""

    def setUp(self):
        self.script = INIT_SCRIPT.read_text(encoding="utf-8")

    def test_files_list_includes_teamflow_models_json(self):
        match = re.search(r'\nFILES=\(\n(.*?)\n\)', self.script, re.DOTALL)
        self.assertIsNotNone(
            match, "scripts/install must define a FILES=(...) block"
        )
        entries = [ln.strip() for ln in match.group(1).splitlines()]
        self.assertIn(
            '".teamflow/models.json"', entries,
            'FILES=(...) block must include the literal ".teamflow/models.json" entry',
        )


class HermeticInstallModelsJsonTests(unittest.TestCase):
    """AC 6: a fresh install ships models.json and the manifest lists it."""

    def test_install_ships_models_json_and_manifest_entry(self):
        with tempfile.TemporaryDirectory() as directory:
            tools = _IsolatedTools(Path(directory))
            project = tools.new_git_project("target")
            completed = tools.initialize(project)
            self.assertEqual(completed.returncode, 0, completed.stderr)

            installed = project / ".teamflow" / "models.json"
            self.assertTrue(
                installed.is_file(),
                ".teamflow/models.json must be installed into the target project",
            )

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
            self.assertIn(
                ".teamflow/models.json", keys,
                ".teamflow/manifest.json must list .teamflow/models.json as an installed key",
            )


class RealPiModelResolutionTests(unittest.TestCase):
    """AC 7: real ``pi --list-models`` resolves the registry offline."""

    def setUp(self):
        if shutil.which("pi") is None:
            self.skipTest("pi binary not installed on PATH")

    def test_pi_list_models_resolves_zhipu_provider_and_glm_model(self):
        env = os.environ.copy()
        env["PI_CODING_AGENT_DIR"] = str(ROOT / ".teamflow")
        completed = subprocess.run(
            ["pi", "--list-models"],
            cwd=str(ROOT),
            env=env,
            capture_output=True,
            text=True,
            stdin=subprocess.DEVNULL,
            timeout=30,
        )
        self.assertEqual(
            completed.returncode, 0,
            f"pi --list-models exited {completed.returncode}: "
            f"stdout={completed.stdout!r} stderr={completed.stderr!r}",
        )
        combined = completed.stdout + completed.stderr
        self.assertIn("zhipuai-coding-plan", combined)
        self.assertIn("glm-5.2", combined)


class _IsolatedTools:
    """Hermetic installer fixture mirroring tests/test_pi_wrapper.py."""

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


if __name__ == "__main__":
    unittest.main()
