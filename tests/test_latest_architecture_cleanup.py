"""Requirement tests for the current-architecture cleanup.

These tests pin the post-cleanup footprint of active source, scripts, and docs.
They are written BEFORE the cleanup and are expected to be RED until:

  A) No OpenCode runtime claims and no WORKFLOW_*/OPENCODE_WORKFLOW_* fallback
     remain in active source/docs/scripts; init/bootstrap/setup carry no legacy
     .opencode/.workflow migration, aliases, or tombstones.
  B) skills/observe-loop-monitor is removed; AGENTS.md and README.md describe only
     metadata-only process + phase + artifact observation with no session-file
     access.
  E) The experiments tooling remains installed and checked.
  F) A repository retention helper deletes only .teamflow/runs raw *.ndjson/*.log
     and temporary server-scope-adapter scratch while preserving task-receipts,
     phases/current, tests.patch+lock, memory JSON and sources; it never touches
     .teamflow/sessions/{auth.json,models-store.json} or ~/.teamflow/memory.
  G) The installer, doctor, and docs reflect only the current footprint.

Only this file (a negative-migration test) mentions the retired identifiers.
"""

import os
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
AGENTS_MD = ROOT / "AGENTS.md"
README_MD = ROOT / "README.md"
BOOTSTRAP = SCRIPTS / "bootstrap.sh"
SETUP_MEMORY = SCRIPTS / "setup.sh"
INIT_PROJECT = SCRIPTS / "install.sh"
DOCTOR = SCRIPTS / "doctor.sh"
TEAMFLOW_CLI = ROOT / ".teamflow" / "bin" / "teamflow"
PRUNE_RUNS = SCRIPTS / "clean.py"

LEGACY_ENV_TOKENS = (
    "WORKFLOW_HOME",
    "WORKFLOW_BIN_DIR",
    "WORKFLOW_MEMORY_HOME",
    "WORKFLOW_MEMORY_PROJECT",
    "WORKFLOW_MODEL_STAGE_TIMEOUT_SECONDS",
    "WORKFLOW_MEMORY_MAX_CREATES_PER_RUN",
    "WORKFLOW_PHASE_TIMEOUT_SECONDS",
    "OPENCODE_WORKFLOW_MEMORY_HOME",
)


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


class ActiveScriptsHaveNoLegacyFallbackTests(unittest.TestCase):
    """A: init/bootstrap/setup/doctor carry no legacy fallback or migration."""

    def test_bootstrap_has_no_workflow_env_or_legacy_migration(self):
        source = read(BOOTSTRAP)
        lowered = source.lower()
        self.assertNotIn("opencode", lowered)
        for token in ("WORKFLOW_HOME", "WORKFLOW_BIN_DIR", ".workflow", "agent-workflow-launcher"):
            with self.subTest(token=token):
                self.assertNotIn(token, source)

    def test_setup_memory_has_no_workflow_env_or_legacy_migration(self):
        source = read(SETUP_MEMORY)
        lowered = source.lower()
        self.assertNotIn("opencode", lowered)
        for token in (
            "WORKFLOW_HOME", "WORKFLOW_MEMORY_HOME", "WORKFLOW_MEMORY_PROJECT",
            "OPENCODE_WORKFLOW_MEMORY_HOME", ".workflow/memory", ".opencode-workflow",
        ):
            with self.subTest(token=token):
                self.assertNotIn(token, source)

    def test_init_project_has_no_legacy_migration_or_tombstones(self):
        source = read(INIT_PROJECT)
        lowered = source.lower()
        self.assertNotIn("opencode", lowered)
        for token in (
            "WORKFLOW_HOME", "WORKFLOW_BIN_DIR", ".workflow", ".opencode",
            "opencode.json", "agent-workflow-launcher", "LEGACY_FILES",
            "LEGACY_WORKFLOW_ROOT",
        ):
            with self.subTest(token=token):
                self.assertNotIn(token, source)

    def test_doctor_has_no_workflow_env_fallback(self):
        source = read(DOCTOR)
        lowered = source.lower()
        self.assertNotIn("opencode", lowered)
        for token in ("WORKFLOW_HOME", "WORKFLOW_MEMORY_HOME", "OPENCODE_WORKFLOW_MEMORY_HOME", "WORKFLOW_MEMORY_PROJECT"):
            with self.subTest(token=token):
                self.assertNotIn(token, source)

    def test_teamflow_cli_has_no_workflow_env_fallback(self):
        source = read(TEAMFLOW_CLI)
        self.assertNotIn("WORKFLOW_HOME", source)
        self.assertNotIn("OPENCODE_WORKFLOW", source)


class ActiveDocsHaveNoOpenCodeOrLegacyFallbackTests(unittest.TestCase):
    """A: README and AGENTS.md carry no OpenCode runtime claims or legacy fallback.

    The original guard banned the word "opencode" outright, because the docs
    once claimed OpenCode was the agent runtime. Pi is the runtime, and that
    claim must stay gone.

    opencode is now something different: the observe loop that `teamflow
    server` connects to as an upstream API. Naming it in that role is
    correct, so the guard targets the false runtime claims and the legacy
    config surface instead of the bare word.
    """

    # Phrasings that would reinstate the retired claim, or point at the
    # legacy per-project OpenCode config that no longer exists. Naming
    # opencode as the upstream observe loop is correct and not listed here.
    FORBIDDEN_RUNTIME_CLAIMS = (
        "opencode runtime",
        "opencode agent runtime",
        "opencode harness",
        ".opencode/",
        "opencode.json",
        "opencode.jsonc",
        "opencode.db",
        "@opencode-ai",
    )

    def _assert_clean_docs(self, source: str, label: str):
        lowered = source.lower()
        for claim in self.FORBIDDEN_RUNTIME_CLAIMS:
            with self.subTest(doc=label, claim=claim):
                self.assertNotIn(
                    claim,
                    lowered,
                    f"{label}: '{claim}' reinstates the retired OpenCode runtime claim",
                )
        for token in LEGACY_ENV_TOKENS:
            with self.subTest(doc=label, token=token):
                self.assertNotIn(token, source)
        self.assertNotIn("旧安装迁移", source, f"{label}: legacy migration section removed")

    def test_readme_names_pi_as_the_runtime(self):
        """The positive half: README must still name Pi as the runtime.

        Only README is checked. AGENTS.md defines roles, sequence, and
        engineering rules for the observe loop; naming the runtime is not its
        job, so requiring it there would be a false contract.
        """
        self.assertRegex(
            read(README_MD),
            r"[Pp]i (runtime|agent)|pi-runtime",
            "README.md must state that Pi is the runtime",
        )

    def test_readme_has_no_opencode_or_legacy_fallback(self):
        self._assert_clean_docs(read(README_MD), "README.md")

    def test_agents_has_no_opencode_or_legacy_fallback(self):
        self._assert_clean_docs(read(AGENTS_MD), "AGENTS.md")


class ObserveLoopMonitorRemovedFromDocsTests(unittest.TestCase):
    """B: docs describe metadata-only observation; no monitor/session-file access."""

    def test_readme_and_agents_drop_observe_loop_monitor(self):
        for path, label in ((README_MD, "README.md"), (AGENTS_MD, "AGENTS.md")):
            source = read(path)
            with self.subTest(doc=label):
                self.assertNotIn("observe-loop-monitor", source)
                self.assertNotIn("opencode.db", source)

    def test_docs_keep_metadata_only_event_observation(self):
        """Observation is still metadata-only; the surface is now the event stream.

        ``teamflow wait`` replaced the polling ladder, so requiring the old
        ``teamflow phase status`` here would pin a retired contract.
        """
        for path, label in ((README_MD, "README.md"), (AGENTS_MD, "AGENTS.md")):
            source = read(path)
            with self.subTest(doc=label):
                self.assertIn("teamflow wait", source)
                self.assertIn("teamflow handoff status", source)


class ExperimentsRemainInstalledAndCheckedTests(unittest.TestCase):
    """E: experiments stay installed by the installer and checked by doctor."""

    def test_installer_installs_experiment_commands(self):
        source = read(INIT_PROJECT)
        self.assertIn(".teamflow/experiments/bin/memory-experiment", source)
        self.assertIn(".teamflow/experiments/bin/memory-compare", source)
        self.assertIn(".teamflow/experiments/scripts/compare_stage.py", source)

    def test_doctor_checks_experiment_commands(self):
        source = read(DOCTOR)
        self.assertIn("memory-experiment", source)
        self.assertIn("memory-compare", source)


class RunRetentionHelperTests(unittest.TestCase):
    """F: clean deletes only raw runs scratch, preserving artifacts."""

    def _make_tree(self, root: Path) -> dict[str, Path]:
        runs = root / ".teamflow" / "runs"
        raw_ndjson = runs / "code" / "demo" / "heartbeat.ndjson"
        raw_ndjson.parent.mkdir(parents=True)
        raw_ndjson.write_text('{"ev":"TOPSECRET_RAW_LOG"}\n', encoding="utf-8")
        stage_log = runs / "memory" / "demo" / "compression.log"
        stage_log.parent.mkdir(parents=True)
        stage_log.write_text("TOPSECRET_STAGE_STDOUT\n", encoding="utf-8")
        scope_adapter = runs / "server-scope-adapter" / "scratch.json"
        scope_adapter.parent.mkdir(parents=True)
        scope_adapter.write_text("{}", encoding="utf-8")
        receipt = runs / "task-receipts" / "demo" / "receipt.json"
        receipt.parent.mkdir(parents=True)
        receipt.write_text('{"k":"receipt"}\n', encoding="utf-8")
        current = runs / "code" / "demo" / "phases" / "current.json"
        current.parent.mkdir(parents=True)
        current.write_text('{"phase":"one"}\n', encoding="utf-8")
        patch = runs / "test-patches" / "demo" / "tests.patch"
        patch.parent.mkdir(parents=True)
        patch.write_text("diff --git\n", encoding="utf-8")
        patch_lock = runs / "test-patches" / "demo" / "tests.patch.lock.json"
        patch_lock.write_text('{"schema_version":1}\n', encoding="utf-8")
        capsule = runs / "memory" / "demo" / "00-evidence-capsule.json"
        capsule.write_text('{"kind":"evidence-capsule"}\n', encoding="utf-8")
        manifest = runs / "memory" / "demo" / "manifest.json"
        manifest.write_text('{"run_id":"demo"}\n', encoding="utf-8")
        sources = runs / "memory" / "demo" / "sources" / "NOTE-1.md"
        sources.parent.mkdir(parents=True)
        sources.write_text("source body\n", encoding="utf-8")
        sessions = root / ".teamflow" / "sessions"
        sessions.mkdir(parents=True)
        (sessions / "auth.json").write_text('{"token":"TOPSECRET_AUTH"}\n', encoding="utf-8")
        (sessions / "models-store.json").write_text('{"models":"TOPSECRET_MODELS"}\n', encoding="utf-8")
        return {
            "raw_ndjson": raw_ndjson, "stage_log": stage_log, "scope_adapter": scope_adapter,
            "receipt": receipt, "current": current, "patch": patch, "patch_lock": patch_lock,
            "capsule": capsule, "manifest": manifest, "sources": sources,
        }

    def _home_memory(self, root: Path) -> Path:
        home = root / "home"
        keep = home / ".teamflow" / "memory" / "knowledge" / "keep.md"
        keep.parent.mkdir(parents=True)
        keep.write_text("home memory\n", encoding="utf-8")
        return keep

    def test_helper_exists_and_is_executable(self):
        self.assertTrue(PRUNE_RUNS.is_file(), "scripts/clean must exist")
        self.assertTrue(os.access(PRUNE_RUNS, os.X_OK), "scripts/clean must be executable")

    def test_dry_run_deletes_nothing(self):
        self.assertTrue(PRUNE_RUNS.is_file())
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = self._make_tree(root)
            keep = self._home_memory(root)
            env = os.environ.copy()
            env["HOME"] = str(root / "home")
            completed = subprocess.run(
                [str(PRUNE_RUNS), "--dry-run", "--root", str(root)],
                env=env, text=True, capture_output=True, timeout=30,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            for label, path in paths.items():
                with self.subTest(artifact=label):
                    self.assertTrue(path.is_file(), f"{label} must survive --dry-run")
            self.assertTrue(keep.is_file(), "~/.teamflow/memory must be excluded even on --dry-run")

    def test_apply_deletes_only_raw_scratch_and_preserves_artifacts(self):
        self.assertTrue(PRUNE_RUNS.is_file())
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = self._make_tree(root)
            keep = self._home_memory(root)
            auth = root / ".teamflow" / "sessions" / "auth.json"
            models = root / ".teamflow" / "sessions" / "models-store.json"
            env = os.environ.copy()
            env["HOME"] = str(root / "home")
            completed = subprocess.run(
                [str(PRUNE_RUNS), "--root", str(root)],
                env=env, text=True, capture_output=True, timeout=30,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertFalse(paths["raw_ndjson"].is_file(), "raw *.ndjson must be deleted")
            self.assertFalse(paths["stage_log"].is_file(), "raw *.log must be deleted")
            self.assertFalse(paths["scope_adapter"].is_file(), "server-scope-adapter scratch must be deleted")
            for label in ("receipt", "current", "patch", "patch_lock", "capsule", "manifest", "sources"):
                with self.subTest(artifact=label):
                    self.assertTrue(paths[label].is_file(), f"{label} must be preserved")
            self.assertEqual(auth.read_text(encoding="utf-8"), '{"token":"TOPSECRET_AUTH"}\n')
            self.assertEqual(models.read_text(encoding="utf-8"), '{"models":"TOPSECRET_MODELS"}\n')
            self.assertTrue(keep.is_file(), "~/.teamflow/memory must be excluded")
            self.assertEqual(keep.read_text(encoding="utf-8"), "home memory\n")


class InstallerDoctorFootprintTests(unittest.TestCase):
    """G: installer/doctor/docs reflect only the current footprint."""

    def test_installer_does_not_install_removed_monitor(self):
        self.assertNotIn("observe-loop-monitor", read(INIT_PROJECT))

    def test_readme_layout_reflects_current_footprint(self):
        source = read(README_MD)
        self.assertIn("experiments/", source)
        self.assertIn("server/", source)
        self.assertNotIn(".opencode", source)


if __name__ == "__main__":
    unittest.main()
