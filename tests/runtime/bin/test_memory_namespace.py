import os
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
SETUP_MEMORY = ROOT / "scripts/setup.sh"


def write_fake_basic_memory(bin_dir: Path) -> Path:
    log = bin_dir.parent / "basic-memory.log"
    tool = bin_dir / "basic-memory"
    tool.write_text(
        """#!/bin/sh
printf '%s\\n' "$*" >> "${FAKE_BASIC_MEMORY_LOG:?}"
if [ "${1:-} ${2:-}" = "project info" ]; then
  exit 1
fi
if [ "${1:-}" = "status" ]; then
  printf '{}\\n'
fi
exit 0
""",
        encoding="utf-8",
    )
    tool.chmod(0o755)
    return log


def isolated_environment(home: Path, bin_dir: Path, log: Path) -> dict[str, str]:
    env = os.environ.copy()
    for key in list(env):
        if key.startswith(("TEAMFLOW_", "WORKFLOW_", "OPENCODE_WORKFLOW_")):
            env.pop(key)
    env.pop("BASIC_MEMORY_PROJECT", None)
    env.update(
        {
            "HOME": str(home),
            "PATH": f"{bin_dir}:{env['PATH']}",
            "FAKE_BASIC_MEMORY_LOG": str(log),
        }
    )
    return env


def run_setup(env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(SETUP_MEMORY)],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        timeout=30,
    )


class TeamflowMemoryNamespaceTests(unittest.TestCase):
    def test_default_ignores_old_memory_and_uses_teamflow_project(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            home = root / "home"
            bin_dir = root / "bin"
            home.mkdir()
            bin_dir.mkdir()
            log = write_fake_basic_memory(bin_dir)
            legacy = home / ".workflow/memory"
            (legacy / "knowledge").mkdir(parents=True)
            marker = legacy / "knowledge/marker.md"
            marker.write_text("preserve me", encoding="utf-8")

            completed = run_setup(isolated_environment(home, bin_dir, log))
            self.assertEqual(completed.returncode, 0, completed.stderr)

            current = home / ".teamflow/memory"
            self.assertTrue(legacy.exists())
            self.assertEqual(marker.read_text(encoding="utf-8"), "preserve me")
            self.assertTrue((current / "knowledge").is_dir())
            self.assertFalse((current / "knowledge/marker.md").exists())
            calls = log.read_text(encoding="utf-8")
            self.assertRegex(
                calls,
                r"(?m)^project add teamflow .*/\.teamflow/memory/knowledge --default --local$",
            )
            self.assertNotIn("project add workflow ", calls)

    def test_primary_teamflow_memory_env_wins_over_legacy_compatibility_env(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            home = root / "home"
            bin_dir = root / "bin"
            home.mkdir()
            bin_dir.mkdir()
            log = write_fake_basic_memory(bin_dir)
            env = isolated_environment(home, bin_dir, log)
            primary = root / "primary-memory"
            env.update(
                {
                    "TEAMFLOW_MEMORY_HOME": str(primary),
                    "TEAMFLOW_MEMORY_PROJECT": "primary-teamflow",
                    "WORKFLOW_MEMORY_HOME": str(root / "legacy-memory"),
                    "WORKFLOW_MEMORY_PROJECT": "legacy-workflow",
                    "OPENCODE_WORKFLOW_MEMORY_HOME": str(root / "older-memory"),
                }
            )

            completed = run_setup(env)
            self.assertEqual(completed.returncode, 0, completed.stderr)
            calls = log.read_text(encoding="utf-8")
            self.assertRegex(
                calls,
                r"(?m)^project add primary-teamflow .*/primary-memory/knowledge --default --local$",
            )
            self.assertNotIn("legacy-workflow", calls)
            self.assertNotIn("legacy-memory", calls)
            self.assertNotIn("older-memory", calls)

    def test_setup_script_declares_teamflow_memory_as_primary_interface(self):
        source = SETUP_MEMORY.read_text(encoding="utf-8")
        self.assertIn("TEAMFLOW_MEMORY_HOME", source)
        self.assertIn("TEAMFLOW_MEMORY_PROJECT", source)
        self.assertIn("$HOME/.teamflow", source)
