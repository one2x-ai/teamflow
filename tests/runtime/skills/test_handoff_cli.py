"""Requirement tests for the ``teamflow handoff`` mechanical state CLI.

``docs/handoff-runtime-refactor-design.md`` replaces the flat ``phase``
pipeline with handoffs: one work unit moved from a delegator to a receiver,
whose state the receiver maintains through the CLI until a terminal status.

The iron law behind every assertion here: state changes and state queries
are *programmatic*. A handoff's status must be answerable by ``ls`` or one
small JSON read, must never require a model to "remember" to write it, and
must never be reachable by hand-writing a state file.

All paths are relative to the repository root
``ROOT = Path(__file__).resolve().parents[3]``.
"""

import json
import os
import re
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
TEAMFLOW_BIN = ROOT / ".teamflow" / "bin" / "teamflow"
HANDOFF_CLI = (
    ROOT / ".teamflow" / "skills" / "write-handoff" / "scripts" / "handoff_state.py"
)
PHASE_CLI = (
    ROOT / ".teamflow" / "skills" / "plan-change" / "scripts" / "phase_state.py"
)

TIMEOUT = 60

#: The single top-level blocked enum (design S2.3). CLI and policy layer
#: must not disagree: the two budget values and the four non-budget values
#: live in one namespace.
BLOCKED_REASONS = (
    "CONTEXT_BUDGET_EXCEEDED",
    "RECALL_BUDGET_EXCEEDED",
    "DELEGATION_ARTIFACT_MISSING",
    "OUTPUT_TRUNCATED",
    "PROVIDER_FAILURE",
    "USER_CANCELLED",
)

#: Event kinds (design S2.4).
EVENT_KINDS = (
    "run_started",
    "run_finished",
    "handoff_opened",
    "handoff_finished",
    "artifact_written",
    "runner_exited",
)

EVENT_NAME = re.compile(
    r"^(?P<seq>\d{5})--(?P<subject>[a-z0-9-]+)--(?P<kind>[a-z_]+)--(?P<status>[A-Z_]+)\.json$"
)

HANDOFF_BODY = """- Goal: teamflow handoff CLI records state mechanically.
- Scope: .teamflow/skills/write-handoff/scripts/handoff_state.py
- Acceptance: 1. open writes state.json.
"""


def clean_env(home: Path) -> dict:
    env = dict(os.environ)
    for key in list(env):
        if key.startswith(("TEAMFLOW_", "WORKFLOW_", "OPENCODE_WORKFLOW_")):
            env.pop(key)
    for key in (
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "ALL_PROXY",
        "http_proxy",
        "https_proxy",
        "all_proxy",
    ):
        env.pop(key, None)
    env["HOME"] = str(home)
    return env


class HandoffFixture:
    """A temporary ``.teamflow/runs/code`` tree driven through the real CLI."""

    def __init__(self, directory: Path, run_id: str = "run-test-0001"):
        self.root = directory
        self.home = directory / "home"
        self.home.mkdir(parents=True, exist_ok=True)
        self.runs = directory / ".teamflow" / "runs"
        self.code = self.runs / "code"
        self.code.mkdir(parents=True, exist_ok=True)
        self.run_id = run_id

    @property
    def run_dir(self) -> Path:
        return self.code / self.run_id

    def cli(self, *args, body: str | None = None, run_id: str | None = None):
        argv = ["python3", str(HANDOFF_CLI), *args, "--runs-dir", str(self.code)]
        resolved = self.run_id if run_id is None else run_id
        if resolved:
            argv += ["--run-id", resolved]
        return subprocess.run(
            argv,
            cwd=str(self.root),
            input=body,
            env=clean_env(self.home),
            capture_output=True,
            text=True,
            timeout=TIMEOUT,
        )

    def open_handoff(self, role="test-writer", body=HANDOFF_BODY, extra=()):
        completed = self.cli(
            "handoff", "open", "--role", role, "--body-file", "-", *extra, body=body
        )
        assert completed.returncode == 0, completed.stderr
        return json.loads(completed.stdout)

    def receipt_file(self, status="PASS"):
        """A minimal valid receipt; delegated handoffs cannot finish without one."""
        path = self.root / f"receipt-{status}.json"
        path.write_text(
            json.dumps({"status": status, "command": "true", "exit_code": 0}),
            encoding="utf-8",
        )
        return str(path)

    def finish_child(self, handoff_id, status="PASS"):
        completed = self.cli(
            "handoff", "finish", "--id", handoff_id, "--status", status,
            "--summary", "s", "--receipt", self.receipt_file(status),
        )
        assert completed.returncode == 0, completed.stderr
        return completed

    def events(self):
        directory = self.run_dir / "events"
        if not directory.is_dir():
            return []
        return sorted(p.name for p in directory.iterdir() if p.is_file())

    def spool(self):
        directory = self.code / "_spool"
        if not directory.is_dir():
            return []
        return sorted(
            p.name for p in directory.iterdir() if p.is_file() and EVENT_NAME.match(p.name)
        )

    def state(self, handoff_id):
        path = self.run_dir / "handoffs" / handoff_id / "state.json"
        return json.loads(path.read_text(encoding="utf-8"))


class CliExistenceTests(unittest.TestCase):
    def test_handoff_cli_exists(self):
        self.assertTrue(
            HANDOFF_CLI.is_file(),
            "the handoff state CLI must live with the write-handoff skill so "
            "it installs with the runtime",
        )

    def test_teamflow_dispatches_handoff_subcommand(self):
        text = TEAMFLOW_BIN.read_text(encoding="utf-8")
        self.assertIn("handoff", text, "teamflow must dispatch a handoff subcommand")
        self.assertIn("handoff_state.py", text)

    def test_teamflow_dispatches_agents_subcommand(self):
        text = TEAMFLOW_BIN.read_text(encoding="utf-8")
        self.assertRegex(
            text,
            r'"agents"',
            "teamflow must dispatch `agents` for the derived registry view",
        )


class BlockedReasonEnumTests(unittest.TestCase):
    """S2.3: one top-level enum, no CLI/policy split."""

    def test_all_six_reasons_accepted_by_handoff_finish(self):
        for reason in BLOCKED_REASONS:
            with self.subTest(reason=reason), tempfile.TemporaryDirectory() as directory:
                fixture = HandoffFixture(Path(directory))
                opened = fixture.open_handoff()
                completed = fixture.cli(
                    "handoff",
                    "finish",
                    "--id",
                    opened["handoff_id"],
                    "--status",
                    "BLOCKED",
                    "--blocked-reason",
                    reason,
                    "--summary",
                    "blocked for the test",
                )
                self.assertEqual(completed.returncode, 0, completed.stderr)
                state = fixture.state(opened["handoff_id"])
                self.assertEqual(state["status"], "blocked")
                self.assertEqual(state["blocked"]["reason"], reason)

    def test_unknown_reason_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = HandoffFixture(Path(directory))
            opened = fixture.open_handoff()
            completed = fixture.cli(
                "handoff",
                "finish",
                "--id",
                opened["handoff_id"],
                "--status",
                "BLOCKED",
                "--blocked-reason",
                "SOMETHING_ELSE",
                "--summary",
                "s",
            )
            self.assertNotEqual(
                completed.returncode, 0, "an unlisted reason must be rejected"
            )

    def test_blocked_requires_a_reason(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = HandoffFixture(Path(directory))
            opened = fixture.open_handoff()
            completed = fixture.cli(
                "handoff", "finish", "--id", opened["handoff_id"],
                "--status", "BLOCKED", "--summary", "s",
            )
            self.assertNotEqual(
                completed.returncode, 0, "BLOCKED without a reason is not a receipt"
            )

    def test_both_reasons_can_be_recorded(self):
        """Truncation *and* a missing artifact are two facts, not one."""
        with tempfile.TemporaryDirectory() as directory:
            fixture = HandoffFixture(Path(directory))
            opened = fixture.open_handoff()
            completed = fixture.cli(
                "handoff", "finish", "--id", opened["handoff_id"],
                "--status", "BLOCKED",
                "--blocked-reason", "OUTPUT_TRUNCATED",
                "--blocked-reason", "DELEGATION_ARTIFACT_MISSING",
                "--summary", "s",
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            blocked = fixture.state(opened["handoff_id"])["blocked"]
            self.assertEqual(blocked["reason"], "OUTPUT_TRUNCATED")
            self.assertEqual(
                blocked["reasons"],
                ["OUTPUT_TRUNCATED", "DELEGATION_ARTIFACT_MISSING"],
                "both reasons must survive as an ordered list",
            )

    def test_budget_reason_keeps_budget_failure_detail(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = HandoffFixture(Path(directory))
            opened = fixture.open_handoff()
            completed = fixture.cli(
                "handoff", "finish", "--id", opened["handoff_id"],
                "--status", "BLOCKED",
                "--blocked-reason", "CONTEXT_BUDGET_EXCEEDED",
                "--budget-limit", "1000",
                "--budget-used", "1200",
                "--budget-remaining", "0",
                "--protected-component", "memory-context",
                "--required-action", "split the task",
                "--summary", "s",
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            blocked = fixture.state(opened["handoff_id"])["blocked"]
            self.assertEqual(blocked["reason"], "CONTEXT_BUDGET_EXCEEDED")
            self.assertEqual(blocked["budget_failure"]["budget"]["limit"], 1000)
            self.assertEqual(blocked["budget_failure"]["budget"]["used"], 1200)

    def test_legacy_phase_alias_shares_the_same_enum(self):
        """`teamflow phase` survives one version and must not disagree."""
        text = PHASE_CLI.read_text(encoding="utf-8")
        self.assertNotIn(
            '"CONTEXT_BUDGET_EXCEEDED", "RECALL_BUDGET_EXCEEDED")',
            text,
            "the legacy alias must import the shared enum, not restate a "
            "two-value subset",
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / ".teamflow" / "runs" / "code").mkdir(parents=True)
            env = clean_env(root)
            start = subprocess.run(
                ["python3", str(PHASE_CLI), "start", "--run-id", "r", "--phase", "p",
                 "--owner", "planner"],
                cwd=str(root), env=env, capture_output=True, text=True, timeout=TIMEOUT,
            )
            self.assertEqual(start.returncode, 0, start.stderr)
            finish = subprocess.run(
                ["python3", str(PHASE_CLI), "finish", "--run-id", "r",
                 "--status", "BLOCKED", "--summary", "s",
                 "--block-reason", "DELEGATION_ARTIFACT_MISSING"],
                cwd=str(root), env=env, capture_output=True, text=True, timeout=TIMEOUT,
            )
            self.assertEqual(
                finish.returncode, 0,
                "the legacy alias must accept the full enum: " + finish.stderr,
            )
            receipt = json.loads(
                (root / ".teamflow" / "runs" / "code" / "r" / "phases" / "p.json")
                .read_text(encoding="utf-8")
            )
            self.assertEqual(
                receipt["blocked"]["reason"],
                "DELEGATION_ARTIFACT_MISSING",
                "the reason must be a top-level field, not buried in summary prose",
            )


class HandoffOpenTests(unittest.TestCase):
    """S2.2/S2.3: the delegator writes the body, the CLI registers state."""

    def test_open_materializes_directory_layout(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = HandoffFixture(Path(directory))
            opened = fixture.open_handoff()
            handoff_id = opened["handoff_id"]
            base = fixture.run_dir / "handoffs" / handoff_id
            self.assertTrue((base / "handoff.md").is_file())
            self.assertTrue((base / "state.json").is_file())
            self.assertEqual(
                (base / "handoff.md").read_text(encoding="utf-8"),
                HANDOFF_BODY,
                "the CLI must persist the delegator's body verbatim",
            )

    def test_open_requires_a_body(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = HandoffFixture(Path(directory))
            completed = fixture.cli("handoff", "open", "--role", "coder")
            self.assertNotEqual(
                completed.returncode, 0,
                "a handoff without a body is the vague request the contract bans",
            )

    def test_open_creates_active_sentinel(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = HandoffFixture(Path(directory))
            opened = fixture.open_handoff()
            sentinel = fixture.run_dir / "active" / opened["handoff_id"]
            self.assertTrue(
                sentinel.exists(),
                "'what is running now' must be answerable by ls active/",
            )

    def test_open_state_is_open_status(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = HandoffFixture(Path(directory))
            opened = fixture.open_handoff()
            state = fixture.state(opened["handoff_id"])
            self.assertEqual(state["status"], "open")
            self.assertEqual(state["role"], "test-writer")

    def test_handoff_ids_are_unique_for_the_same_role(self):
        """Concurrent same-named work must not overwrite one receipt."""
        with tempfile.TemporaryDirectory() as directory:
            fixture = HandoffFixture(Path(directory))
            first = fixture.open_handoff()
            second = fixture.open_handoff()
            self.assertNotEqual(first["handoff_id"], second["handoff_id"])
            self.assertRegex(first["handoff_id"], r"^[a-z0-9-]+$")

    def test_open_records_lineage_for_a_child(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = HandoffFixture(Path(directory))
            parent = fixture.open_handoff(role="planner")
            child = fixture.open_handoff(
                role="coder", extra=("--parent-id", parent["handoff_id"])
            )
            state = fixture.state(child["handoff_id"])
            self.assertEqual(
                state["lineage"]["parent_handoff_id"], parent["handoff_id"]
            )
            self.assertEqual(state["depth"], 1, "a child handoff runs at depth 1")

    def test_root_handoff_depth_is_zero(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = HandoffFixture(Path(directory))
            root = fixture.open_handoff(role="planner")
            self.assertEqual(fixture.state(root["handoff_id"])["depth"], 0)

    def test_scope_is_parsed_from_the_body(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = HandoffFixture(Path(directory))
            opened = fixture.open_handoff()
            self.assertIn(
                ".teamflow/skills/write-handoff/scripts/handoff_state.py",
                fixture.state(opened["handoff_id"])["scope"],
            )

    def test_overlapping_scope_is_reported_as_a_conflict(self):
        """The guard rail for parallel task_group: intersecting scopes warn."""
        with tempfile.TemporaryDirectory() as directory:
            fixture = HandoffFixture(Path(directory))
            fixture.open_handoff(role="coder")
            completed = fixture.cli(
                "handoff", "open", "--role", "test-writer", "--body-file", "-",
                body=HANDOFF_BODY,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            payload = json.loads(completed.stdout)
            self.assertIn(
                ".teamflow/skills/write-handoff/scripts/handoff_state.py",
                payload["scope_conflicts"],
            )
            self.assertIn("warning", completed.stderr.lower())

    def test_disjoint_scope_reports_no_conflict(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = HandoffFixture(Path(directory))
            fixture.open_handoff(role="coder")
            payload = fixture.open_handoff(
                role="test-writer",
                body="- Goal: other work.\n- Scope: server/src/other.ts\n",
            )
            self.assertEqual(payload["scope_conflicts"], [])


class HandoffLifecycleTests(unittest.TestCase):
    """S2.3: open -> running -> done(PASS|FAIL) | blocked(reason)."""

    def test_start_moves_open_to_running(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = HandoffFixture(Path(directory))
            opened = fixture.open_handoff()
            completed = fixture.cli(
                "handoff", "start", "--id", opened["handoff_id"], "--pid", str(os.getpid())
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            state = fixture.state(opened["handoff_id"])
            self.assertEqual(state["status"], "running")
            self.assertEqual(state["pid"], os.getpid())

    def test_finish_pass_writes_done_and_clears_sentinel(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = HandoffFixture(Path(directory))
            opened = fixture.open_handoff()
            fixture.cli("handoff", "start", "--id", opened["handoff_id"])
            completed = fixture.cli(
                "handoff", "finish", "--id", opened["handoff_id"],
                "--status", "PASS", "--summary", "done",
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            state = fixture.state(opened["handoff_id"])
            self.assertEqual(state["status"], "done")
            self.assertEqual(state["result"], "PASS")
            self.assertFalse(
                (fixture.run_dir / "active" / opened["handoff_id"]).exists(),
                "a finished handoff must leave the active set",
            )

    def test_finishing_twice_is_rejected(self):
        """A terminal handoff is immutable: no silent overwrite."""
        with tempfile.TemporaryDirectory() as directory:
            fixture = HandoffFixture(Path(directory))
            opened = fixture.open_handoff()
            first = fixture.cli(
                "handoff", "finish", "--id", opened["handoff_id"],
                "--status", "PASS", "--summary", "done",
            )
            self.assertEqual(first.returncode, 0, first.stderr)
            second = fixture.cli(
                "handoff", "finish", "--id", opened["handoff_id"],
                "--status", "FAIL", "--summary", "overwrite attempt",
            )
            self.assertNotEqual(
                second.returncode, 0,
                "re-finishing a terminal handoff must be an illegal transition",
            )
            self.assertEqual(fixture.state(opened["handoff_id"])["result"], "PASS")

    def test_unknown_handoff_id_fails_loudly(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = HandoffFixture(Path(directory))
            completed = fixture.cli(
                "handoff", "finish", "--id", "h99999-ghost",
                "--status", "PASS", "--summary", "s",
            )
            self.assertNotEqual(completed.returncode, 0)

    def test_current_json_is_never_created(self):
        """S2.7: the single cursor is abolished; it cannot come back."""
        with tempfile.TemporaryDirectory() as directory:
            fixture = HandoffFixture(Path(directory))
            first = fixture.open_handoff(role="coder")
            second = fixture.open_handoff(role="test-runner")
            fixture.cli("handoff", "start", "--id", first["handoff_id"])
            fixture.cli("handoff", "start", "--id", second["handoff_id"])
            self.assertFalse(
                (fixture.run_dir / "current.json").exists(),
                "current.json is a single cursor and conflicts with parallel work",
            )
            active = sorted(p.name for p in (fixture.run_dir / "active").iterdir())
            self.assertEqual(
                active, sorted([first["handoff_id"], second["handoff_id"]]),
                "both concurrent handoffs must be visible at once",
            )


class ReceiptSchemaTests(unittest.TestCase):
    """S2.5: receipts become validated artifacts, not assistant prose."""

    def _receipt(self, **overrides):
        receipt = {
            "status": "FAIL",
            "command": "python3 tests/test_x.py",
            "exit_code": 1,
            "failed_checks": ["test_x"],
            "error_excerpt": "AssertionError: 1 != 0",
            "reproduction": "python3 tests/test_x.py",
            "diagnosis": "diagnosis: missing behavior",
            "next_owner": "coder",
            "expected_red": True,
        }
        receipt.update(overrides)
        return receipt

    def _finish_with(self, fixture, handoff_id, receipt, status="FAIL", role="test-runner"):
        path = fixture.root / "receipt-input.json"
        path.write_text(json.dumps(receipt), encoding="utf-8")
        return fixture.cli(
            "handoff", "finish", "--id", handoff_id, "--status", status,
            "--receipt", str(path), "--summary", "s",
        )

    def test_valid_test_runner_receipt_is_stored(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = HandoffFixture(Path(directory))
            opened = fixture.open_handoff(role="test-runner")
            completed = self._finish_with(fixture, opened["handoff_id"], self._receipt())
            self.assertEqual(completed.returncode, 0, completed.stderr)
            stored = json.loads(
                (fixture.run_dir / "handoffs" / opened["handoff_id"] / "receipt.json")
                .read_text(encoding="utf-8")
            )
            self.assertEqual(stored["exit_code"], 1)
            self.assertEqual(stored["next_owner"], "coder")

    def test_receipt_missing_required_field_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = HandoffFixture(Path(directory))
            opened = fixture.open_handoff(role="test-runner")
            broken = self._receipt()
            del broken["exit_code"]
            completed = self._finish_with(fixture, opened["handoff_id"], broken)
            self.assertNotEqual(
                completed.returncode, 0,
                "a test-runner receipt without exit_code is not a receipt",
            )
            self.assertIn("exit_code", completed.stderr)

    def test_receipt_status_must_match_declared_status(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = HandoffFixture(Path(directory))
            opened = fixture.open_handoff(role="test-runner")
            completed = self._finish_with(
                fixture, opened["handoff_id"], self._receipt(status="PASS"), status="FAIL"
            )
            self.assertNotEqual(
                completed.returncode, 0,
                "a receipt claiming PASS cannot finish a FAIL handoff",
            )

    def test_non_json_receipt_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = HandoffFixture(Path(directory))
            opened = fixture.open_handoff(role="test-runner")
            path = fixture.root / "prose.json"
            path.write_text("The tests failed, I think.", encoding="utf-8")
            completed = fixture.cli(
                "handoff", "finish", "--id", opened["handoff_id"], "--status", "FAIL",
                "--receipt", str(path), "--summary", "s",
            )
            self.assertNotEqual(
                completed.returncode, 0, "prose must not pass as a structured receipt"
            )

    def test_long_error_excerpt_spills_to_evidence_and_leaves_a_ref(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = HandoffFixture(Path(directory))
            opened = fixture.open_handoff(role="test-runner")
            completed = self._finish_with(
                fixture, opened["handoff_id"], self._receipt(error_excerpt="E" * 5000)
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            stored = json.loads(
                (fixture.run_dir / "handoffs" / opened["handoff_id"] / "receipt.json")
                .read_text(encoding="utf-8")
            )
            self.assertLessEqual(
                len(stored["error_excerpt"]), 2000,
                "error_excerpt has a hard 2000-character cap",
            )
            reference = stored.get("error_excerpt_ref")
            self.assertTrue(reference, "the spilled body must be reachable by ref")
            spilled = fixture.runs / reference
            self.assertTrue(spilled.is_file(), f"{spilled} must exist")
            self.assertEqual(len(spilled.read_text(encoding="utf-8")), 5000)

    def test_batch_receipt_validates_every_entry(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = HandoffFixture(Path(directory))
            opened = fixture.open_handoff(role="test-runner")
            batch = {
                "status": "FAIL",
                "receipts": [
                    {"id": "focused", **self._receipt()},
                    {"id": "regression", "status": "PASS", "command": "x"},
                ],
            }
            completed = self._finish_with(fixture, opened["handoff_id"], batch)
            self.assertNotEqual(
                completed.returncode, 0,
                "the second batch entry has no exit_code and must be rejected",
            )


class EventProtocolTests(unittest.TestCase):
    """S2.4: one event per file, filename carries the metadata."""

    def test_open_emits_handoff_opened_event(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = HandoffFixture(Path(directory))
            opened = fixture.open_handoff()
            names = fixture.events()
            self.assertEqual(len(names), 1, names)
            match = EVENT_NAME.match(names[0])
            self.assertIsNotNone(match, f"{names[0]} must match the event name grammar")
            self.assertEqual(match.group("seq"), "00001")
            self.assertEqual(match.group("subject"), opened["handoff_id"])
            self.assertEqual(match.group("kind"), "handoff_opened")
            self.assertEqual(match.group("status"), "OPEN")

    def test_finish_emits_handoff_finished_with_status_in_the_name(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = HandoffFixture(Path(directory))
            opened = fixture.open_handoff()
            fixture.cli(
                "handoff", "finish", "--id", opened["handoff_id"],
                "--status", "FAIL", "--summary", "s",
            )
            names = fixture.events()
            match = EVENT_NAME.match(names[1])
            self.assertEqual(match.group("kind"), "handoff_finished")
            self.assertEqual(match.group("status"), "FAIL")

    def test_run_stream_is_a_self_contained_history(self):
        """Folding events/ must answer "what happened" for the whole run."""
        with tempfile.TemporaryDirectory() as directory:
            fixture = HandoffFixture(Path(directory))
            root = fixture.open_handoff(role="planner")
            fixture.cli(
                "handoff", "finish", "--id", root["handoff_id"],
                "--status", "FAIL", "--summary", "s",
            )
            self.assertEqual(
                [EVENT_NAME.match(name).group("kind") for name in fixture.events()],
                ["handoff_opened", "handoff_finished", "run_finished"],
            )

    def test_child_finish_does_not_close_the_run(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = HandoffFixture(Path(directory))
            root = fixture.open_handoff(role="planner")
            child = fixture.open_handoff(
                role="coder", extra=("--parent-id", root["handoff_id"])
            )
            fixture.finish_child(child["handoff_id"])
            self.assertEqual(
                [EVENT_NAME.match(name).group("kind") for name in fixture.events()],
                ["handoff_opened", "handoff_opened", "handoff_finished"],
                "only the root handoff finishing means the run finished",
            )

    def test_sequence_is_monotonic_and_zero_padded(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = HandoffFixture(Path(directory))
            for _ in range(3):
                fixture.open_handoff()
            names = fixture.events()
            self.assertEqual(
                [EVENT_NAME.match(n).group("seq") for n in names],
                ["00001", "00002", "00003"],
                "zero padding makes lexicographic order time order",
            )

    def test_event_body_is_small_and_carries_a_ref(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = HandoffFixture(Path(directory))
            opened = fixture.open_handoff()
            path = fixture.run_dir / "events" / fixture.events()[0]
            raw = path.read_bytes()
            self.assertLess(
                len(raw), 1024, "event bodies stay small; large content goes to evidence/"
            )
            payload = json.loads(raw.decode("utf-8"))
            self.assertEqual(payload["subject"], opened["handoff_id"])
            self.assertIn(
                "handoffs/", payload["ref"],
                "the body points at the detail file instead of inlining it",
            )

    def test_tmp_staging_directory_is_left_empty(self):
        """Atomic delivery: write into tmp/, rename into events/."""
        with tempfile.TemporaryDirectory() as directory:
            fixture = HandoffFixture(Path(directory))
            fixture.open_handoff()
            staging = fixture.run_dir / "tmp"
            self.assertTrue(staging.is_dir(), "tmp/ staging directory must exist")
            self.assertEqual(
                sorted(p.name for p in staging.iterdir()), [],
                "a completed delivery leaves nothing half-written in tmp/",
            )

    def test_cli_uses_rename_for_delivery(self):
        source = HANDOFF_CLI.read_text(encoding="utf-8")
        self.assertTrue(
            "os.replace" in source or "os.rename" in source,
            "delivery must be an atomic rename, not a direct write into events/",
        )

    def test_cli_uses_flock_for_sequence_allocation(self):
        source = HANDOFF_CLI.read_text(encoding="utf-8")
        self.assertIn(
            "flock", source, "sequence allocation must be lock-protected"
        )

    def test_event_names_stay_within_filesystem_limits(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = HandoffFixture(Path(directory))
            fixture.open_handoff(role="a" * 300)
            for name in fixture.events():
                with self.subTest(name=name):
                    self.assertLessEqual(len(name.encode("utf-8")), 255)
                    self.assertIsNotNone(EVENT_NAME.match(name))

    def test_artifact_written_event_requires_the_artifact_to_exist(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = HandoffFixture(Path(directory))
            opened = fixture.open_handoff()
            missing = fixture.cli(
                "handoff", "finish", "--id", opened["handoff_id"], "--status", "PASS",
                "--summary", "s", "--artifact", "does/not/exist.patch",
            )
            self.assertNotEqual(
                missing.returncode, 0,
                "claiming an artifact that is not on disk must fail mechanically",
            )

    def test_artifact_written_event_is_emitted_for_a_real_artifact(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = HandoffFixture(Path(directory))
            opened = fixture.open_handoff()
            artifact = fixture.root / "tests.patch"
            artifact.write_text("diff --git a b\n", encoding="utf-8")
            completed = fixture.cli(
                "handoff", "finish", "--id", opened["handoff_id"], "--status", "PASS",
                "--summary", "s", "--artifact", "tests.patch",
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            kinds = [EVENT_NAME.match(n).group("kind") for n in fixture.events()]
            self.assertIn("artifact_written", kinds)


class SpoolAndRunEventTests(unittest.TestCase):
    """S2.7: cross-run discovery uses the run-level spool."""

    def test_run_start_emits_spool_and_run_events(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = HandoffFixture(Path(directory))
            completed = fixture.cli(
                "handoff", "run-start", "--role", "planner", "--pid", str(os.getpid())
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            spool = fixture.spool()
            self.assertEqual(len(spool), 1, spool)
            match = EVENT_NAME.match(spool[0])
            self.assertEqual(match.group("kind"), "run_started")
            self.assertEqual(match.group("subject"), fixture.run_id)
            self.assertTrue(
                (fixture.run_dir / "runner.json").is_file(),
                "the depth-0 process must record its pid in runner.json",
            )

    def test_finishing_the_root_handoff_emits_run_finished(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = HandoffFixture(Path(directory))
            root = fixture.open_handoff(role="planner")
            child = fixture.open_handoff(
                role="coder", extra=("--parent-id", root["handoff_id"])
            )
            fixture.finish_child(child["handoff_id"])
            self.assertEqual(
                fixture.spool(), [],
                "a child handoff finishing is not the run finishing",
            )
            fixture.cli(
                "handoff", "finish", "--id", root["handoff_id"],
                "--status", "PASS", "--summary", "s",
            )
            spool = fixture.spool()
            self.assertEqual(len(spool), 1, spool)
            match = EVENT_NAME.match(spool[0])
            self.assertEqual(match.group("kind"), "run_finished")
            self.assertEqual(match.group("status"), "PASS")

    def test_runner_exited_event_is_emitted_for_depth_zero(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = HandoffFixture(Path(directory))
            completed = fixture.cli(
                "handoff", "runner-exited", "--pid", "424242",
                "--role", "planner", "--depth", "0",
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            kinds = [EVENT_NAME.match(n).group("kind") for n in fixture.events()]
            self.assertIn("runner_exited", kinds)

    def test_runner_exited_is_not_emitted_for_depth_one(self):
        """Signal/noise split: a child's exit is already observed by its parent."""
        with tempfile.TemporaryDirectory() as directory:
            fixture = HandoffFixture(Path(directory))
            completed = fixture.cli(
                "handoff", "runner-exited", "--pid", "424243",
                "--role", "coder", "--depth", "1",
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertEqual(
                fixture.events(), [],
                "only a depth-0 exit means nobody is left to report",
            )


class StatusAndListTests(unittest.TestCase):
    def test_status_without_id_returns_every_active_handoff(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = HandoffFixture(Path(directory))
            first = fixture.open_handoff(role="coder")
            second = fixture.open_handoff(role="test-runner")
            completed = fixture.cli("handoff", "status")
            self.assertEqual(completed.returncode, 0, completed.stderr)
            payload = json.loads(completed.stdout)
            self.assertIsInstance(payload, list)
            self.assertEqual(
                sorted(entry["handoff_id"] for entry in payload),
                sorted([first["handoff_id"], second["handoff_id"]]),
            )

    def test_status_with_id_returns_one_receipt(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = HandoffFixture(Path(directory))
            opened = fixture.open_handoff()
            completed = fixture.cli("handoff", "status", "--id", opened["handoff_id"])
            self.assertEqual(completed.returncode, 0, completed.stderr)
            payload = json.loads(completed.stdout)
            self.assertEqual(payload["handoff_id"], opened["handoff_id"])

    def test_running_handoff_reports_age_and_stale(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = HandoffFixture(Path(directory))
            opened = fixture.open_handoff()
            fixture.cli("handoff", "start", "--id", opened["handoff_id"])
            completed = fixture.cli("handoff", "status", "--id", opened["handoff_id"])
            payload = json.loads(completed.stdout)
            self.assertGreaterEqual(payload["age_seconds"], 0)
            self.assertFalse(payload["stale"], "a fresh handoff is not stale")

    def test_list_active_filters_terminal_handoffs(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = HandoffFixture(Path(directory))
            done = fixture.open_handoff(role="coder")
            live = fixture.open_handoff(role="test-runner")
            fixture.cli(
                "handoff", "finish", "--id", done["handoff_id"],
                "--status", "PASS", "--summary", "s",
            )
            completed = fixture.cli("handoff", "list", "--active")
            self.assertEqual(completed.returncode, 0, completed.stderr)
            payload = json.loads(completed.stdout)
            self.assertEqual(
                [entry["handoff_id"] for entry in payload], [live["handoff_id"]]
            )

    def test_list_reports_title_from_the_goal_line(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = HandoffFixture(Path(directory))
            fixture.open_handoff()
            payload = json.loads(fixture.cli("handoff", "list").stdout)
            self.assertEqual(
                payload[0]["title"],
                "teamflow handoff CLI records state mechanically.",
                "the Goal line is the free title; no model call needed",
            )


class ModelsMayNotWriteStateDirectlyTests(unittest.TestCase):
    """The mechanical/semantic boundary must be visible in the prompts."""

    def test_shared_rules_forbid_hand_writing_state(self):
        text = (ROOT / ".teamflow" / "AGENTS.md").read_text(encoding="utf-8")
        self.assertIn("state.json", text)
        self.assertRegex(
            text,
            r"(?i)never (hand-?write|write)[^.]*state\.json"
            r"|(?i)do not (hand-?write|write)[^.]*state\.json",
            ".teamflow/AGENTS.md must forbid writing state files by hand",
        )

    def test_shared_rules_name_the_handoff_cli(self):
        text = (ROOT / ".teamflow" / "AGENTS.md").read_text(encoding="utf-8")
        self.assertIn("teamflow handoff", text)


if __name__ == "__main__":
    unittest.main()
