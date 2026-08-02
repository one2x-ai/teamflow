"""Requirement tests for the deploy-to-playground GitHub Actions workflow.

The workflow file ``.github/workflows/deploy.yml`` must be copied verbatim
from the authoritative skill asset.  These tests assert that the copy is
byte-for-byte identical to the asset and that the resulting workflow
satisfies the structural and secret-safety requirements verified by the
planner.

When the workflow file does not yet exist every test below fails with a
clear assertion — no import or parse error is raised.
"""

import hashlib
import os
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "deploy.yml"

DEFAULT_ASSET = "/Users/wenshiqi/.codex/skills/deploy-to-playground/assets/deploy.yml"
ASSET_PATH = Path(os.environ.get("ONE2X_DEPLOY_WORKFLOW_ASSET", DEFAULT_ASSET))

EXPECTED_SHA256 = "3cde16ac025c7fb210db973b8aea89ec50541f5c26155f21b9a291be391a807f"
EXPECTED_SIZE = 7739

# GitHub PAT literal prefixes that must never appear in the raw workflow text.
PAT_PREFIXES = ("ghp_", "github_pat_", "gho_", "ghu_", "ghs_", "ghr_")


class _WorkflowGuard(unittest.TestCase):
    """Common setUp that fails with a clear message when the file is missing."""

    def setUp(self):
        self.assertTrue(
            WORKFLOW.is_file(),
            f"Expected workflow at {WORKFLOW} but it does not exist. "
            "The deploy-to-playground asset must be copied here.",
        )


def _yaml():
    """Parse the workflow YAML (PyYAML 6.x)."""
    import yaml

    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


def _on_key(data):
    """Return the workflow trigger configuration.

    PyYAML parses the bare ``on:`` key as ``True``, so check both spellings.
    """
    return data.get("on", data.get(True))


class WorkflowExistsTests(_WorkflowGuard):
    """The workflow file must exist in the repository."""

    def test_deploy_yml_exists(self):
        self.assertTrue(WORKFLOW.is_file())


class ByteForByteIdenticalTests(_WorkflowGuard):
    """The installed workflow must be byte-for-byte identical to the asset."""

    def test_raw_bytes_equal_asset_bytes(self):
        installed = WORKFLOW.read_bytes()
        asset = ASSET_PATH.read_bytes()
        self.assertEqual(installed, asset)

    def test_sha256_matches_known_hash(self):
        digest = hashlib.sha256(WORKFLOW.read_bytes()).hexdigest()
        self.assertEqual(digest, EXPECTED_SHA256)

    def test_size_matches_known_size(self):
        self.assertEqual(len(WORKFLOW.read_bytes()), EXPECTED_SIZE)


class MainTriggerTests(_WorkflowGuard):
    """The workflow must trigger on push to ``main``."""

    def test_triggers_on_push_to_main(self):
        on = _on_key(_yaml())
        if isinstance(on, dict):
            branches = on.get("push", {}).get("branches", [])
        elif isinstance(on, list):
            branches = on
        else:
            branches = []
        self.assertIn("main", branches)


class OidcDeployTests(_WorkflowGuard):
    """The deploy job must use OIDC (id-token: write)."""

    def test_id_token_write_permission(self):
        data = _yaml()
        permissions = data["jobs"]["deploy"].get("permissions", {})
        self.assertEqual(permissions.get("id-token"), "write")

    def test_uses_configure_aws_credentials(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("aws-actions/configure-aws-credentials", text)


class EcrRepositoryTests(_WorkflowGuard):
    """ECR_REPOSITORY must derive from the GitHub repository name."""

    def test_ecr_repository_uses_repo_name(self):
        env = _yaml().get("env", {})
        self.assertEqual(
            env.get("ECR_REPOSITORY"),
            "${{ github.event.repository.name }}",
        )


class BuildPushImageTests(_WorkflowGuard):
    """A step must build and push the Docker image."""

    def test_uses_docker_build_push_action_with_push_true(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("docker/build-push-action", text)
        self.assertIn("push: true", text)


class SecretSafetyTests(_WorkflowGuard):
    """The workflow must reference the GitOps PAT secret and never embed tokens."""

    def test_references_one2x_gitops_pat(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("secrets.ONE2X_GITOPS_PAT", text)

    def test_no_embedded_pat_prefixes(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        for prefix in PAT_PREFIXES:
            with self.subTest(prefix=prefix):
                self.assertNotIn(prefix, text)

    def test_no_bearer_literal_token(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        scrubbed = text.replace("secrets.ONE2X_GITOPS_PAT", "")
        self.assertNotRegex(scrubbed, r"Bearer\s+[A-Za-z0-9_]")


if __name__ == "__main__":
    unittest.main()
