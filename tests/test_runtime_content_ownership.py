"""Requirement tests for .teamflow/ content ownership and orphan cleanup.

Contracts:

1. `.teamflow/` holds Pi-agent runtime content only. Metadata for other AI
   harnesses (OpenAI Agents interface files, Codex/Claude project configs)
   has no consumer here: nothing in this repository reads it, Pi does not
   discover it, and shipping it writes dead files into every business
   project. Such files are removed and then ignored so a future harness can
   drop its own config locally without it becoming a managed artifact.

2. The installer removes orphaned managed files. When a file leaves the
   template, a project that installed the earlier version must not keep the
   stale copy — otherwise agents load skills and prompts that no longer
   exist upstream.
"""

import json
import os
import re
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SKILLS = ROOT / ".teamflow" / "skills"
RUNTIME_GITIGNORE = ROOT / ".teamflow" / ".gitignore"
INSTALL = ROOT / "scripts" / "install.sh"

# Interface/config formats owned by other AI harnesses.
FOREIGN_HARNESS_FILES = ("openai.yaml", "openai.yml")


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.is_file() else ""


class NoForeignHarnessMetadataTests(unittest.TestCase):
    """No skill carries interface metadata for another harness."""

    def test_no_openai_interface_files_remain(self):
        offenders = []
        for name in FOREIGN_HARNESS_FILES:
            offenders.extend(str(p.relative_to(ROOT)) for p in SKILLS.rglob(name))
        self.assertEqual(
            offenders,
            [],
            "these files have no consumer in this repository and must be "
            f"removed: {offenders}",
        )

    def test_no_empty_agents_directories_remain(self):
        """The agents/ wrappers existed only to hold that metadata."""
        offenders = [
            str(p.relative_to(ROOT))
            for p in SKILLS.glob("*/agents")
            if p.is_dir() and not any(p.iterdir())
        ]
        self.assertEqual(offenders, [], f"empty directories: {offenders}")

    def test_nothing_references_the_removed_metadata(self):
        """No code path still expects the removed metadata.

        A dangling read would make the removal a silent break. Scoped to
        executable sources: docs legitimately name the file to explain that
        it is ignored, and the tests asserting the removal must name it too.
        """
        exempt_names = {
            Path(__file__).name,
            "test_artifact_hygiene.py",
        }
        for pattern in ("*.py", "*.sh", "*.ts"):
            for path in ROOT.rglob(pattern):
                relative = path.relative_to(ROOT)
                parts = relative.parts
                if "node_modules" in parts or "runs" in parts:
                    continue
                if parts[0] == ".git":
                    continue
                if relative.name in exempt_names:
                    continue
                with self.subTest(file=str(relative)):
                    self.assertNotIn("openai.yaml", read(path))

    def test_no_skill_directory_still_carries_the_metadata(self):
        """The skills tree itself must be free of the removed files."""
        for pattern in ("*.md", "*.yaml", "*.yml"):
            for path in SKILLS.rglob(pattern):
                with self.subTest(file=str(path.relative_to(ROOT))):
                    self.assertNotIn("openai.yaml", read(path))


class RuntimeGitignoreTests(unittest.TestCase):
    """.teamflow/.gitignore keeps foreign harness config untracked."""

    def test_gitignore_exists(self):
        self.assertTrue(RUNTIME_GITIGNORE.is_file())

    def test_gitignore_is_tracked(self):
        """The exclusions are a shared convention, so they must be in Git.

        An earlier version listed `.gitignore` among its own patterns, which
        made the file untracked: a fresh clone silently lost every rule
        below. Tracking it is what makes the convention shared.
        """
        tracked = subprocess.run(
            ["git", "ls-files", ".teamflow/.gitignore"],
            cwd=ROOT, capture_output=True, text=True, check=True,
        ).stdout.strip()
        self.assertEqual(
            tracked,
            ".teamflow/.gitignore",
            ".teamflow/.gitignore must be tracked so clones inherit the rules",
        )

    def test_gitignore_does_not_ignore_itself(self):
        lines = {
            line.strip()
            for line in read(RUNTIME_GITIGNORE).splitlines()
            if line.strip() and not line.startswith("#")
        }
        self.assertNotIn(
            ".gitignore",
            lines,
            "ignoring itself would hide the shared rules from every clone",
        )

    def test_gitignore_is_not_installed(self):
        """It documents this repository's layout, not a project's."""
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory) / "target"
            project.mkdir()
            subprocess.run(["git", "init", "-q", str(project)], check=True)
            env = os.environ.copy()
            for key in list(env):
                if key.startswith(("TEAMFLOW_", "WORKFLOW_", "OPENCODE_WORKFLOW_")):
                    env.pop(key)
            completed = subprocess.run(
                [str(INSTALL), "--dry-run", str(project)],
                cwd=ROOT, env=env, capture_output=True, text=True,
                stdin=subprocess.DEVNULL, timeout=60,
            )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertNotIn(
            ".teamflow/.gitignore",
            completed.stdout.splitlines(),
            ".teamflow/.gitignore must not be a managed file",
        )

    def test_ignores_openai_interface_metadata(self):
        text = read(RUNTIME_GITIGNORE)
        self.assertRegex(
            text,
            r"(?m)^.*openai\.ya?ml\s*$",
            ".teamflow/.gitignore must ignore OpenAI interface metadata",
        )

    def test_ignores_other_harness_project_config(self):
        """Codex/Claude style local config must not become managed content."""
        text = read(RUNTIME_GITIGNORE)
        for entry in (".codex", "CLAUDE.md"):
            with self.subTest(entry=entry):
                self.assertIn(entry, text)

    def test_documents_why_entries_are_ignored(self):
        self.assertRegex(
            read(RUNTIME_GITIGNORE),
            r"(?m)^#\s*\S",
            ".teamflow/.gitignore must explain what it excludes and why",
        )


class InstallerRemovesOrphansTests(unittest.TestCase):
    """A file that leaves the template also leaves installed projects."""

    def test_install_script_prunes_unmanaged_files(self):
        text = read(INSTALL)
        self.assertRegex(
            text,
            r"orphan|no longer managed|stale managed",
            "install.sh must document orphan pruning",
        )

    def test_orphan_removal_is_hash_guarded(self):
        """Only an untouched previous copy may be deleted."""
        text = read(INSTALL)
        prune_index = text.find("orphan")
        self.assertGreater(prune_index, -1)
        self.assertIn(
            "manifest_hash",
            text[prune_index:],
            "orphan pruning must compare the recorded manifest hash before "
            "deleting, so user-modified files survive",
        )

    def test_removes_orphan_on_reinstall(self):
        """End to end: a managed file dropped from FILES is deleted."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            home = root / "home"
            launchers = root / "bin"
            home.mkdir()
            launchers.mkdir()
            project = root / "target"
            project.mkdir()
            subprocess.run(["git", "init", "-q", str(project)], check=True)

            env = os.environ.copy()
            for key in list(env):
                if key.startswith(("TEAMFLOW_", "WORKFLOW_", "OPENCODE_WORKFLOW_")):
                    env.pop(key)
            env.update(
                {
                    "HOME": str(home),
                    "TEAMFLOW_HOME": str(home / ".teamflow"),
                    "TEAMFLOW_BIN_DIR": str(launchers),
                }
            )

            first = subprocess.run(
                [str(INSTALL), str(project)],
                cwd=ROOT, env=env, capture_output=True, text=True,
                stdin=subprocess.DEVNULL, timeout=180,
            )
            self.assertEqual(first.returncode, 0, first.stderr)

            manifest_path = project / ".teamflow" / "manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

            # Simulate a file that the template used to manage: record it in
            # the manifest with the hash of the content we write, so it is an
            # untouched managed copy rather than a user edit.
            orphan_relative = ".teamflow/skills/plan-change/agents/openai.yaml"
            orphan_path = project / orphan_relative
            orphan_path.parent.mkdir(parents=True, exist_ok=True)
            orphan_body = "interface:\n  display_name: \"Stale\"\n"
            orphan_path.write_text(orphan_body, encoding="utf-8")
            digest = subprocess.run(
                ["shasum", "-a", "256", str(orphan_path)],
                capture_output=True, text=True, check=True,
            ).stdout.split()[0]
            manifest["files"][orphan_relative] = digest
            manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

            second = subprocess.run(
                [str(INSTALL), str(project)],
                cwd=ROOT, env=env, capture_output=True, text=True,
                stdin=subprocess.DEVNULL, timeout=180,
            )
            self.assertEqual(second.returncode, 0, second.stderr)

            self.assertFalse(
                orphan_path.exists(),
                "reinstall must delete a managed file that left the template",
            )
            refreshed = json.loads(manifest_path.read_text(encoding="utf-8"))
            self.assertNotIn(orphan_relative, refreshed["files"])

    def test_preserves_user_modified_orphan(self):
        """A stale path the user edited is kept, with a warning."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            home = root / "home"
            launchers = root / "bin"
            home.mkdir()
            launchers.mkdir()
            project = root / "target"
            project.mkdir()
            subprocess.run(["git", "init", "-q", str(project)], check=True)

            env = os.environ.copy()
            for key in list(env):
                if key.startswith(("TEAMFLOW_", "WORKFLOW_", "OPENCODE_WORKFLOW_")):
                    env.pop(key)
            env.update(
                {
                    "HOME": str(home),
                    "TEAMFLOW_HOME": str(home / ".teamflow"),
                    "TEAMFLOW_BIN_DIR": str(launchers),
                }
            )

            first = subprocess.run(
                [str(INSTALL), str(project)],
                cwd=ROOT, env=env, capture_output=True, text=True,
                stdin=subprocess.DEVNULL, timeout=180,
            )
            self.assertEqual(first.returncode, 0, first.stderr)

            manifest_path = project / ".teamflow" / "manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            orphan_relative = ".teamflow/skills/plan-change/agents/openai.yaml"
            orphan_path = project / orphan_relative
            orphan_path.parent.mkdir(parents=True, exist_ok=True)
            orphan_path.write_text("user edited this\n", encoding="utf-8")
            # Manifest records a different hash, marking it user-modified.
            manifest["files"][orphan_relative] = "0" * 64
            manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

            second = subprocess.run(
                [str(INSTALL), str(project)],
                cwd=ROOT, env=env, capture_output=True, text=True,
                stdin=subprocess.DEVNULL, timeout=180,
            )
            self.assertEqual(second.returncode, 0, second.stderr)
            self.assertTrue(
                orphan_path.exists(),
                "a user-modified stale file must be preserved, not deleted",
            )
            self.assertIn("user edited this", orphan_path.read_text(encoding="utf-8"))


class TeamflowDirectoryIsNotNpmProjectTests(unittest.TestCase):
    """``.teamflow/`` must not become an npm project.

    At one point ``.teamflow/`` carried a ``package.json`` declaring
    ``@opencode-ai/plugin`` plus a 61 MB ``node_modules`` (46 MB of it
    ``effect``).  Nothing imported any of it: extensions import only
    ``@earendil-works/*``, ``typebox``, ``node:*``, and relative modules,
    which Pi resolves from its own global installation.  These files were
    removed.  This test prevents them from returning.
    """

    def test_no_package_json(self):
        self.assertFalse(
            (ROOT / ".teamflow" / "package.json").exists(),
            ".teamflow/ must not carry a package.json",
        )

    def test_no_package_lock(self):
        self.assertFalse(
            (ROOT / ".teamflow" / "package-lock.json").exists(),
            ".teamflow/ must not carry a package-lock.json",
        )

    def test_no_node_modules(self):
        self.assertFalse(
            (ROOT / ".teamflow" / "node_modules").exists(),
            ".teamflow/ must not carry a node_modules directory",
        )

    def test_no_bun_lock(self):
        self.assertFalse(
            (ROOT / ".teamflow" / "bun.lock").exists(),
            ".teamflow/ must not carry a bun.lock",
        )

    def test_extensions_imports_resolve_without_npm(self):
        """Prove the premise: extensions never needed a local npm install.

        Every import specifier in ``.teamflow/extensions/**/*.ts`` must
        resolve through Pi's own installation, Bun's built-in modules, or
        relative paths — never through a local ``node_modules``.  If an
        extension ever adopts a dependency outside this allow-list, this
        test flags that the no-npm rule needs revisiting.
        """
        import_patterns = (
            re.compile(r'from\s+"([^"]+)"'),
            re.compile(r"from\s+'([^']+)"),
        )
        offenders = []
        for path in (ROOT / ".teamflow" / "extensions").rglob("*.ts"):
            if "node_modules" in path.parts:
                continue
            source = path.read_text(encoding="utf-8")
            for pattern in import_patterns:
                for match in pattern.finditer(source):
                    specifier = match.group(1)
                    if (
                        specifier.startswith("@earendil-works/")
                        or specifier == "typebox"
                        # bun:test is Bun's built-in test module, resolved by
                        # the runtime, not by npm or a local node_modules.
                        or specifier == "bun:test"
                        or specifier.startswith("node:")
                        or specifier.startswith(".")
                    ):
                        continue
                    offenders.append(
                        f"{path.relative_to(ROOT)}: {specifier!r}"
                    )
        # ``typebox`` resolves through Pi's installation; if that becomes
        # incidental rather than guaranteed, this test's allowed list
        # should be revisited.
        self.assertEqual(
            offenders,
            [],
            "extension imports must resolve without a local npm install; "
            "if a specifier outside the allow-list is intentional, the "
            "no-npm rule needs revisiting: " + ", ".join(offenders),
        )


if __name__ == "__main__":
    unittest.main()
