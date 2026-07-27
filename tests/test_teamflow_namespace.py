import hashlib
import json
import os
import re
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def write_executable(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    path.chmod(0o755)


class IsolatedTools:
    def __init__(self, root: Path):
        self.root = root
        self.home = root / "home"
        self.bin = root / "fake-bin"
        self.launchers = root / "launchers"
        self.log = root / "tool.log"
        self.home.mkdir(parents=True)
        self.bin.mkdir(parents=True)
        write_executable(
            self.bin / "opencode",
            """#!/bin/sh
if [ "${1:-}" = "--version" ]; then
  printf '1.18.4\\n'
elif [ "${1:-}" = "debug" ] && [ "${2:-}" = "skill" ]; then
  printf 'plan-change\\nbasic-memory-cli\\n'
fi
exit 0
""",
        )
        write_executable(
            self.bin / "basic-memory",
            """#!/bin/sh
printf '%s\\n' "$*" >> "${FAKE_TOOL_LOG:?}"
if [ "${1:-} ${2:-}" = "project info" ]; then
  exit 1
fi
if [ "${1:-}" = "status" ]; then
  printf '{}\\n'
fi
exit 0
""",
        )

    def environment(self) -> dict[str, str]:
        env = os.environ.copy()
        for key in list(env):
            if key.startswith(("TEAMFLOW_", "WORKFLOW_", "OPENCODE_WORKFLOW_")):
                env.pop(key)
        env.update(
            {
                "HOME": str(self.home),
                "PATH": f"{self.bin}:{env['PATH']}",
                "TEAMFLOW_HOME": str(self.home / ".teamflow"),
                "TEAMFLOW_BIN_DIR": str(self.launchers),
                "FAKE_TOOL_LOG": str(self.log),
            }
        )
        return env

    def new_git_project(self, name: str) -> Path:
        project = self.root / name
        project.mkdir()
        subprocess.run(["git", "init", "-q", str(project)], check=True)
        return project

    def initialize(self, project: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [str(ROOT / "scripts/init-project.sh"), *arguments, str(project)],
            cwd=ROOT,
            env=self.environment(),
            text=True,
            capture_output=True,
            timeout=30,
        )


class PublicNamespaceTests(unittest.TestCase):
    def test_repository_and_global_launcher_are_teamflow(self):
        launcher = ROOT / "scripts/teamflow"
        self.assertTrue(launcher.is_file())
        self.assertTrue(os.access(launcher, os.X_OK))
        self.assertFalse((ROOT / "scripts/workflow").exists())

        bootstrap = (ROOT / "scripts/bootstrap.sh").read_text(encoding="utf-8")
        self.assertIn("scripts/teamflow", bootstrap)
        self.assertRegex(bootstrap, r'LAUNCHER_PATH="\$LAUNCHER_DIR/teamflow"')
        self.assertNotIn("scripts/workflow", bootstrap)
        self.assertNotRegex(bootstrap, r'LAUNCHER_PATH="\$LAUNCHER_DIR/workflow"')

        launcher_text = launcher.read_text(encoding="utf-8")
        self.assertIn(".teamflow/bin/teamflow", launcher_text)
        self.assertNotIn(".workflow/bin/workflow", launcher_text)

    def test_runtime_templates_and_primary_environment_names_are_teamflow(self):
        expected = [
            ROOT / ".teamflow/bin/teamflow",
            ROOT / ".teamflow/skills/extract-memory/scripts/run_pipeline.py",
        ]
        for path in expected:
            with self.subTest(path=path):
                self.assertTrue(path.is_file())
        self.assertFalse((ROOT / ".workflow/config.json").exists())

        sources = [
            ROOT / "scripts/bootstrap.sh",
            ROOT / "scripts/init-project.sh",
            ROOT / "scripts/setup-memory.sh",
            ROOT / "scripts/doctor.sh",
            ROOT / "scripts/teamflow",
            ROOT / ".teamflow/bin/teamflow",
            ROOT / ".teamflow/skills/extract-memory/scripts/run_pipeline.py",
        ]
        combined = "\n".join(path.read_text(encoding="utf-8") for path in sources)
        for name in (
            "TEAMFLOW_HOME",
            "TEAMFLOW_BIN_DIR",
            "TEAMFLOW_MEMORY_HOME",
            "TEAMFLOW_MEMORY_PROJECT",
            "TEAMFLOW_MODEL_STAGE_TIMEOUT_SECONDS",
        ):
            with self.subTest(name=name):
                self.assertIn(name, combined)
        self.assertIn(".teamflow", combined)
        self.assertIn('"teamflow"', combined)

    def test_docs_agents_and_skills_expose_only_the_teamflow_interface(self):
        files = [ROOT / "README.md", ROOT / "AGENTS.md"]
        files.extend(sorted((ROOT / "skills").rglob("*.md")))
        files.extend(sorted((ROOT / ".teamflow/agents").glob("*.md")))
        files.extend(sorted((ROOT / ".teamflow/skills").rglob("*.md")))
        self.assertGreater(len(files), 10)

        allowed_legacy_markers = re.compile(
            r"legacy|deprecated|compatib|migrat|previous|old|旧|兼容|迁移|历史",
            re.IGNORECASE,
        )
        stale_patterns = (
            re.compile(r"(?<![\w-])\.workflow(?:/|\b)"),
            re.compile(r"\bscripts/workflow\b"),
            re.compile(r"\bworkflow\s+(?:run|command|memory|memory-capture|test-patch|source-check|phase|debug)\b"),
            re.compile(r"`workflow`"),
            re.compile(r"\bWORKFLOW_(?:HOME|BIN_DIR|MEMORY_HOME|MEMORY_PROJECT|MODEL_STAGE_TIMEOUT_SECONDS)\b"),
        )
        violations = []
        combined = []
        for path in files:
            text = path.read_text(encoding="utf-8")
            combined.append(text)
            for number, line in enumerate(text.splitlines(), 1):
                if allowed_legacy_markers.search(line):
                    continue
                if any(pattern.search(line) for pattern in stale_patterns):
                    violations.append(f"{path.relative_to(ROOT)}:{number}")
        self.assertEqual(violations, [], f"unmarked stale public identifiers: {violations}")

        documentation = "\n".join(combined)
        for value in (
            ".teamflow/",
            "teamflow test-patch",
            "TEAMFLOW_HOME",
            "TEAMFLOW_BIN_DIR",
            "TEAMFLOW_MEMORY_HOME",
            "TEAMFLOW_MEMORY_PROJECT",
            "TEAMFLOW_MODEL_STAGE_TIMEOUT_SECONDS",
        ):
            with self.subTest(value=value):
                self.assertIn(value, documentation)


class InstallerNamespaceTests(unittest.TestCase):
    def test_fresh_install_uses_only_teamflow_runtime_and_launcher(self):
        with tempfile.TemporaryDirectory() as directory:
            tools = IsolatedTools(Path(directory))
            project = tools.new_git_project("target")
            completed = tools.initialize(project)
            self.assertEqual(completed.returncode, 0, completed.stderr)

            runtime = project / ".teamflow/bin/teamflow"
            self.assertTrue(runtime.is_file())
            self.assertTrue(os.access(runtime, os.X_OK))
            self.assertFalse((project / ".workflow").exists())
            manifest = json.loads(
                (project / ".teamflow/manifest.json").read_text(encoding="utf-8")
            )
            self.assertEqual(manifest["source"], "wenshiqi0/teamflow")
            self.assertTrue(manifest["files"])
            self.assertTrue(all(path.startswith(".teamflow/") for path in manifest["files"]))

            ignore_lines = (project / ".gitignore").read_text(encoding="utf-8").splitlines()
            self.assertIn(".teamflow/", ignore_lines)
            self.assertNotIn(".workflow/", ignore_lines)
            self.assertTrue((tools.launchers / "teamflow").is_file())
            self.assertFalse((tools.launchers / "workflow").exists())

    def install_legacy_runtime(
        self, project: Path, *, recorded: bytes, current: bytes | None = None
    ) -> None:
        runtime = project / ".workflow/bin/workflow"
        runtime.parent.mkdir(parents=True)
        runtime.write_bytes(recorded if current is None else current)
        runtime.chmod(0o755)
        manifest = {
            "schema_version": 2,
            "source": "wenshiqi0/workflow",
            "files": {
                ".workflow/bin/workflow": hashlib.sha256(recorded).hexdigest(),
            },
        }
        (project / ".workflow/manifest.json").write_text(
            json.dumps(manifest), encoding="utf-8"
        )
        (project / ".gitignore").write_text(
            "# Local coding workflow runtime\n.workflow/\n", encoding="utf-8"
        )

    def test_old_runtime_is_ignored_during_current_install(self):
        with tempfile.TemporaryDirectory() as directory:
            tools = IsolatedTools(Path(directory))
            project = tools.new_git_project("managed")
            self.install_legacy_runtime(project, recorded=b"#!/bin/sh\nexit 0\n")

            completed = tools.initialize(project)
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertTrue((project / ".workflow/bin/workflow").is_file())
            self.assertTrue((project / ".teamflow/bin/teamflow").is_file())
            ignore_lines = (project / ".gitignore").read_text(encoding="utf-8").splitlines()
            self.assertIn(".teamflow/", ignore_lines)
            self.assertIn(".workflow/", ignore_lines)

    def test_modified_old_runtime_is_preserved_without_backup_or_force(self):
        with tempfile.TemporaryDirectory() as directory:
            tools = IsolatedTools(Path(directory))
            project = tools.new_git_project("modified")
            original = b"#!/bin/sh\nexit 0\n"
            modified = b"#!/bin/sh\nprintf 'user change\\n'\n"
            self.install_legacy_runtime(project, recorded=original, current=modified)

            completed = tools.initialize(project)
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertEqual((project / ".workflow/bin/workflow").read_bytes(), modified)
            self.assertTrue((project / ".teamflow/bin/teamflow").is_file())
            backups = list((tools.home / ".teamflow/backups").glob("**/.workflow/bin/workflow"))
            self.assertEqual(backups, [])
