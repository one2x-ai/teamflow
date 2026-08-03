"""README conciseness and documentation contract tests.

After the container-sidecar refactor, the root ``README.md`` becomes a
concise operator quick start (≤ 260 non-empty lines).  Detailed
deployment/runtime design lives in ``docs/container-sidecar-deployment.md``
and the three pre-existing design docs.  These tests enforce that split.
"""

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
README = ROOT / "README.md"

#: New deployment design document (created by the implementation).
DEPLOYMENT_DOC = "docs/container-sidecar-deployment.md"

#: Pre-existing design docs that the README should still link.
EXISTING_DESIGN_DOCS = (
    "docs/teamflow-memory-context-design.md",
    "docs/teamflow-web-console-design.md",
    "docs/multi-agent-optimization-design.md",
)

#: Maximum allowed README line count (concise operator quick start).
MAX_README_LINES = 260

#: Markers that belong to detailed web-console internals (must be moved to docs/).
INTERNAL_MARKERS = (
    "├── server.ts",          # server/src/ module tree block
    "分四层",                  # four-layer test taxonomy block
)


def _read_readme():
    """Return README text; fail if missing."""
    if not README.exists():
        raise AssertionError(f"README.md not found at {README}")
    return README.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# C1 — README conciseness
# ---------------------------------------------------------------------------


class ReadmeConcisenessTests(unittest.TestCase):
    """C1: README is a concise operator quick start (≤ 260 lines)."""

    def test_readme_line_count_within_limit(self):
        text = _read_readme()
        line_count = len(text.splitlines())
        self.assertLessEqual(
            line_count,
            MAX_README_LINES,
            f"README must be ≤ {MAX_README_LINES} lines, got {line_count}; "
            "move detailed internals to docs/",
        )


# ---------------------------------------------------------------------------
# C2 — README quick-start content markers
# ---------------------------------------------------------------------------


class ReadmeQuickStartContentTests(unittest.TestCase):
    """C2: README contains the required operator quick-start sections."""

    def setUp(self):
        self.text = _read_readme()

    def test_has_install_script_reference(self):
        self.assertIn("install.sh", self.text,
                       "README must reference install.sh for initialization")

    def test_has_uninstall_reference(self):
        self.assertIn("uninstall", self.text.lower(),
                       "README must document uninstall")

    def test_has_teamflow_run_command(self):
        self.assertIn("teamflow run", self.text,
                       "README must show 'teamflow run' usage")

    def test_has_teamflow_command(self):
        self.assertIn("teamflow command", self.text,
                       "README must show 'teamflow command' usage")

    def test_has_doctor_or_debug_reference(self):
        lower = self.text.lower()
        self.assertTrue(
            "doctor" in lower or "debug" in lower,
            "README must reference doctor.sh or teamflow debug for "
            "troubleshooting",
        )


# ---------------------------------------------------------------------------
# C3 — README links design docs
# ---------------------------------------------------------------------------


class ReadmeDocLinksTests(unittest.TestCase):
    """C3: README links the deployment doc and existing design docs."""

    def setUp(self):
        self.text = _read_readme()

    def test_readme_links_deployment_doc(self):
        self.assertIn(
            DEPLOYMENT_DOC,
            self.text,
            f"README must link the deployment design doc ({DEPLOYMENT_DOC})",
        )

    def test_readme_links_existing_design_docs(self):
        for doc in EXISTING_DESIGN_DOCS:
            self.assertIn(
                doc,
                self.text,
                f"README must link existing design doc: {doc}",
            )


# ---------------------------------------------------------------------------
# C4 — README does not keep detailed internals inline
# ---------------------------------------------------------------------------


class ReadmeNoDetailedInternalsTests(unittest.TestCase):
    """C4: README must NOT contain detailed internals moved to docs/."""

    def setUp(self):
        self.text = _read_readme()

    def test_no_server_module_tree(self):
        for marker in INTERNAL_MARKERS:
            self.assertNotIn(
                marker,
                self.text,
                f"README must not contain detailed internal marker "
                f"'{marker}' — move it to docs/",
            )


# ---------------------------------------------------------------------------
# C5 — Deployment design doc exists
# ---------------------------------------------------------------------------


class DeploymentDocExistsTests(unittest.TestCase):
    """C5: The new deployment design document exists under docs/."""

    def test_deployment_doc_exists(self):
        path = ROOT / DEPLOYMENT_DOC
        self.assertTrue(
            path.exists(),
            f"Deployment design doc must exist at {DEPLOYMENT_DOC}",
        )

    def test_deployment_doc_non_empty(self):
        path = ROOT / DEPLOYMENT_DOC
        if not path.exists():
            self.fail(f"Deployment doc not found: {DEPLOYMENT_DOC}")
        text = path.read_text(encoding="utf-8")
        self.assertGreater(len(text.strip()), 0,
                           "Deployment design doc must not be empty")


# ---------------------------------------------------------------------------
# C6 — Deployment doc content markers
# ---------------------------------------------------------------------------


class DeploymentDocContentTests(unittest.TestCase):
    """C6: Deployment doc contains required design content."""

    def setUp(self):
        path = ROOT / DEPLOYMENT_DOC
        if not path.exists():
            self.fail(f"Deployment doc not found: {DEPLOYMENT_DOC}")
        self.text = path.read_text(encoding="utf-8")

    def test_documents_opencode_loopback(self):
        self.assertIn("127.0.0.1:13000", self.text,
                      "Doc must document OpenCode loopback bind 127.0.0.1:13000")

    def test_documents_caddy_public_port(self):
        lower = self.text.lower()
        self.assertTrue(
            ":3000" in self.text or "port 3000" in lower,
            "Doc must document Caddy public port 3000",
        )

    def test_documents_kube_probe_ua(self):
        self.assertIn("kube-probe", self.text,
                      "Doc must document kube-probe UA matching")

    def test_documents_elb_health_checker_ua(self):
        self.assertIn("ELB-HealthChecker", self.text,
                      "Doc must document ELB-HealthChecker UA matching")

    def test_documents_synthetic_ok_response(self):
        self.assertIn("ok", self.text,
                      "Doc must document synthetic 'ok' health response")

    def test_documents_websocket_forwarding(self):
        self.assertIn("WebSocket", self.text,
                      "Doc must document WebSocket forwarding")

    def test_documents_opencode_server_username(self):
        self.assertIn("OPENCODE_SERVER_USERNAME", self.text,
                      "Doc must document OPENCODE_SERVER_USERNAME (Basic Auth)")

    def test_documents_opencode_server_password(self):
        self.assertIn("OPENCODE_SERVER_PASSWORD", self.text,
                      "Doc must document OPENCODE_SERVER_PASSWORD (Basic Auth)")

    def test_documents_workspace_opencode_persistence(self):
        self.assertIn("/workspace/opencode", self.text,
                      "Doc must document /workspace/opencode persistence")

    def test_documents_workspace_teamflow_persistence(self):
        self.assertIn("/workspace/teamflow", self.text,
                      "Doc must document /workspace/teamflow persistence")

    def test_documents_init_or_seed_once(self):
        lower = self.text.lower()
        self.assertTrue(
            "init container" in lower or "init-container" in lower
            or "seed-once" in lower or "seed once" in lower,
            "Doc must document init container or seed-once semantics",
        )

    def test_documents_rollout(self):
        self.assertIn("rollout", self.text.lower(),
                      "Doc must document rollout strategy")

    def test_documents_rollback(self):
        self.assertIn("rollback", self.text.lower(),
                      "Doc must document rollback strategy")

    def test_documents_pvc_persistent_volume(self):
        self.assertIn("PVC", self.text,
                      "Doc must document PVC (persistent volume) usage")

    def test_documents_pod_replacement_preservation(self):
        self.assertIn("Pod", self.text,
                      "Doc must document Pod replacement semantics")
        self.assertTrue(
            "保留" in self.text or "preserv" in self.text.lower()
            or "persist" in self.text.lower(),
            "Doc must document Pod replacement data preservation "
            "(保留 / preserve / persist)",
        )

    def test_documents_standalone_volume_guidance(self):
        lower = self.text.lower()
        self.assertTrue(
            "standalone" in lower or "独立" in self.text,
            "Doc must document standalone container guidance",
        )
        self.assertTrue(
            "volume" in lower or "挂载" in self.text,
            "Doc must document volume / mount guidance for standalone use",
        )


if __name__ == "__main__":
    unittest.main()
