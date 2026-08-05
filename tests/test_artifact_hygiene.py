"""Requirement tests for runtime-artifact hygiene and install isolation.

Two invariants:

1. Runtime state never enters Git. Sessions, run artifacts, credentials,
   caches, and other AI harness config are ignored, so a development clone
   stays free of machine-local state.

2. Installing into a business project ships product files only. Teamflow's
   own development context — its test suites, run artifacts, sessions,
   credentials, design docs, and repository-level instructions — must never
   land in a target repository.
"""

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INSTALL = ROOT / "scripts" / "install.sh"
CLEAN = ROOT / "scripts" / "clean.py"


def check_ignored(relative: str) -> bool:
    """True when Git ignores the path, whether or not it exists."""
    completed = subprocess.run(
        ["git", "check-ignore", "-q", relative],
        cwd=ROOT, capture_output=True, text=True,
    )
    return completed.returncode == 0


class RuntimeStateIsIgnoredTests(unittest.TestCase):
    """Machine-local runtime state stays out of version control."""

    IGNORED_PATHS = (
        ".teamflow/runs/receipt.json",
        ".teamflow/runs/screenshot.png",
        ".teamflow/sessions/session.jsonl",
        ".teamflow/auth.json",
        ".teamflow/models-store.json",
        ".teamflow/.env",
        ".teamflow/node_modules/pkg/index.js",
        ".teamflow/tests/__pycache__/mod.pyc",
        "server/tests/__pycache__/mod.pyc",
        "server/node_modules/pkg/index.js",
        ".pytest_cache/v/cache/lastfailed",
    )

    def test_runtime_state_is_ignored(self):
        for relative in self.IGNORED_PATHS:
            with self.subTest(path=relative):
                self.assertTrue(
                    check_ignored(relative),
                    f"{relative} is runtime state and must be git-ignored",
                )

    def test_foreign_harness_config_is_ignored(self):
        for relative in (
            ".teamflow/CLAUDE.md",
            ".teamflow/.codex/config.toml",
            ".teamflow/skills/plan-change/agents/openai.yaml",
        ):
            with self.subTest(path=relative):
                self.assertTrue(
                    check_ignored(relative),
                    f"{relative} belongs to another harness and must be ignored",
                )

    def test_no_runtime_state_is_currently_tracked(self):
        tracked = subprocess.run(
            ["git", "ls-files", ".teamflow", "server"],
            cwd=ROOT, capture_output=True, text=True, check=True,
        ).stdout.split()
        offenders = [
            path for path in tracked
            if "/runs/" in path
            or "/sessions/" in path
            or "/node_modules/" in path
            or "__pycache__" in path
            or path.endswith(("auth.json", "models-store.json", ".env"))
        ]
        self.assertEqual(offenders, [], f"runtime state is tracked: {offenders}")

    def test_template_content_stays_tracked(self):
        """The ignore rules must not accidentally hide product files."""
        for relative in (
            ".teamflow/AGENTS.md",
            ".teamflow/models.json",
            ".teamflow/agents/planner.md",
            ".teamflow/extensions/memory-context/index.ts",
        ):
            with self.subTest(path=relative):
                self.assertFalse(
                    check_ignored(relative),
                    f"{relative} is product content and must stay tracked",
                )


class InstallShipsProductOnlyTests(unittest.TestCase):
    """A business project receives no Teamflow development context."""

    def _dry_run_manifest(self) -> list[str]:
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
        return [
            line.strip() for line in completed.stdout.splitlines()
            if line.strip() and not line.startswith(".gitignore:")
        ]

    def test_manifest_excludes_development_context(self):
        """Nothing from Teamflow's own development surface is shipped."""
        forbidden_fragments = (
            "/tests/",
            "/runs/",
            "/sessions/",
            "auth.json",
            "models-store.json",
            "/docs/",
            "node_modules",
            "__pycache__",
        )
        offenders = [
            entry for entry in self._dry_run_manifest()
            if any(fragment in entry for fragment in forbidden_fragments)
        ]
        self.assertEqual(
            offenders, [],
            f"installer must ship product files only: {offenders}",
        )

    def test_manifest_stays_below_teamflow(self):
        """Every managed path is inside .teamflow/ — no root-level writes."""
        offenders = [
            entry for entry in self._dry_run_manifest()
            if not entry.startswith(".teamflow/")
        ]
        self.assertEqual(
            offenders, [],
            f"managed files must live below .teamflow/: {offenders}",
        )

    def test_install_does_not_ship_repository_instructions(self):
        """The repo's own AGENTS.md guides outer-loop maintainers, not projects.

        The shared runtime constraints ship as .teamflow/AGENTS.md; the
        repository-level AGENTS.md at the root must not be copied, or a
        business project would inherit instructions about maintaining
        Teamflow itself.
        """
        manifest = self._dry_run_manifest()
        self.assertIn(".teamflow/AGENTS.md", manifest)
        self.assertNotIn("AGENTS.md", manifest)
        self.assertNotIn("README.md", manifest)

    def test_installed_project_has_no_test_or_state_directories(self):
        """End to end: a real install leaves no development directories."""
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
            env.update({
                "HOME": str(home),
                "TEAMFLOW_HOME": str(home / ".teamflow"),
                "TEAMFLOW_BIN_DIR": str(launchers),
            })
            completed = subprocess.run(
                [str(INSTALL), str(project)],
                cwd=ROOT, env=env, capture_output=True, text=True,
                stdin=subprocess.DEVNULL, timeout=180,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)

            runtime = project / ".teamflow"
            for forbidden in ("tests", "sessions", "runs", "server/tests"):
                with self.subTest(directory=forbidden):
                    self.assertFalse(
                        (runtime / forbidden).exists(),
                        f".teamflow/{forbidden} must not be installed",
                    )
            for forbidden in ("auth.json", "models-store.json", ".env"):
                with self.subTest(file=forbidden):
                    self.assertFalse(
                        (runtime / forbidden).exists(),
                        f".teamflow/{forbidden} must not be installed",
                    )
            self.assertFalse(
                (project / "docs").exists(),
                "Teamflow design docs must not be installed",
            )
            # The manifest is the only installer bookkeeping file.
            self.assertTrue((runtime / "manifest.json").is_file())


class CleanRemovesDisposableArtifactsTests(unittest.TestCase):
    """clean.py removes disposable run output, including stray binaries."""

    def test_clean_removes_images_and_scratch(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            runs = project / ".teamflow" / "runs"
            (runs / "memory" / "abc").mkdir(parents=True)
            (runs / "task-receipts" / "run-1").mkdir(parents=True)

            disposable = [
                runs / "trace.ndjson",
                runs / "stage.log",
                runs / "ui-page-7324-browser.png",
            ]
            for path in disposable:
                path.write_text("x", encoding="utf-8")
            keep = runs / "task-receipts" / "run-1" / "receipt.json"
            keep.write_text("{}", encoding="utf-8")

            completed = subprocess.run(
                ["python3", str(CLEAN), "--root", str(project)],
                capture_output=True, text=True, check=True,
            )
            for path in disposable:
                with self.subTest(path=path.name):
                    self.assertFalse(
                        path.exists(),
                        f"{path.name} is disposable and must be removed:\n"
                        f"{completed.stdout}",
                    )
            self.assertTrue(
                keep.is_file(), "verified receipts must survive cleaning"
            )

    def _run_tree(self, project: Path, run_id: str, *, finished: bool):
        """Build one run with the coordination scratch clean.py reasons about."""
        run = project / ".teamflow" / "runs" / "code" / run_id
        handoff = run / "handoffs" / "h00001-planner"
        handoff.mkdir(parents=True)
        state = {
            "handoff_id": "h00001-planner",
            "run_id": run_id,
            "lineage": {"parent_handoff_id": None},
            "status": "done" if finished else "running",
        }
        if finished:
            state["result"] = "PASS"
        (handoff / "state.json").write_text(json.dumps(state), encoding="utf-8")
        (handoff / "receipt.json").write_text('{"status": "PASS"}', encoding="utf-8")
        for name in ("events", "tmp", "liveness"):
            (run / name).mkdir()
        (run / "events" / "00001--h00001-planner--handoff_opened--OPEN.json").write_text(
            "{}", encoding="utf-8"
        )
        if not finished:
            (run / "active").mkdir()
            (run / "active" / "h00001-planner").write_text("", encoding="utf-8")
        return run

    def test_clean_removes_scratch_of_a_finished_run(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            run = self._run_tree(project, "run-done", finished=True)
            subprocess.run(
                ["python3", str(CLEAN), "--root", str(project)],
                capture_output=True, text=True, check=True,
            )
            for name in ("events", "tmp", "liveness"):
                with self.subTest(directory=name):
                    self.assertFalse(
                        (run / name).exists(),
                        f"{name}/ is regenerable once the run is finished",
                    )
            self.assertTrue(
                (run / "handoffs" / "h00001-planner" / "receipt.json").is_file(),
                "handoff receipts are evidence and must survive cleaning",
            )
            self.assertTrue((run / "handoffs" / "h00001-planner" / "state.json").is_file())

    def test_clean_preserves_scratch_of_a_running_run(self):
        """Deleting a live run's counters or sentinels would corrupt its state."""
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            run = self._run_tree(project, "run-live", finished=False)
            subprocess.run(
                ["python3", str(CLEAN), "--root", str(project)],
                capture_output=True, text=True, check=True,
            )
            for name in ("events", "tmp", "liveness", "active"):
                with self.subTest(directory=name):
                    self.assertTrue(
                        (run / name).exists(),
                        f"{name}/ must survive while the run is in flight",
                    )

    def test_clean_dry_run_preserves_finished_run_scratch(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            run = self._run_tree(project, "run-done", finished=True)
            subprocess.run(
                ["python3", str(CLEAN), "--root", str(project), "--dry-run"],
                capture_output=True, text=True, check=True,
            )
            self.assertTrue((run / "events").is_dir(), "--dry-run must not delete")

    def test_clean_dry_run_changes_nothing(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            runs = project / ".teamflow" / "runs"
            runs.mkdir(parents=True)
            target = runs / "trace.ndjson"
            target.write_text("x", encoding="utf-8")
            subprocess.run(
                ["python3", str(CLEAN), "--root", str(project), "--dry-run"],
                capture_output=True, text=True, check=True,
            )
            self.assertTrue(target.is_file(), "--dry-run must not delete")


if __name__ == "__main__":
    unittest.main()
