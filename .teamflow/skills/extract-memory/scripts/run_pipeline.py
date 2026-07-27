#!/usr/bin/env python3
import argparse
import hashlib
import json
import os
import re
import signal
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


STAGES = [
    ("compression", "memory-compressor", "00-evidence-capsule.json", "10-compressed.json"),
    ("extraction", "memory-extractor", "10-compressed.json", "20-extracted.json"),
    ("formatting", "memory-formatter", "20-extracted.json", "30-candidates.json"),
]

EMOTION_LABELS = {
    "neutral", "confusion", "correction", "frustration", "urgency", "approval",
    "boundary_assertion", "preference_assertion", "concern",
}
EMOTION_ACTIONS = {"ignore", "retain_signal", "propose_boundary", "propose_preference", "record_conflict"}

PROTECTED_LITERAL_RE = re.compile(r"\b(?=[A-Za-z0-9]{15,}\b)(?=[A-Za-z0-9]*\d)[A-Za-z0-9]+\b")
DATED_ID_RE = re.compile(r"\b[a-z][a-z0-9_.-]*-\d{6}\b")
SECRET_RE = re.compile(r"(?i)(?:api[_-]?key|authorization|bearer|secret)\s*[:=]\s*[^\s,;]+|\bsk-[A-Za-z0-9_-]{12,}")
MAX_CREATES_PER_RUN = int(os.environ.get("TEAMFLOW_MEMORY_MAX_CREATES_PER_RUN", "8"))


def fail(message: str) -> None:
    print(f"error: {message}", file=sys.stderr)
    raise SystemExit(1)


def write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def parse_model_stage_timeout(value: str | None) -> int | None:
    if value is None or not value.strip():
        return None
    try:
        timeout = int(value)
    except ValueError as exc:
        raise ValueError("TEAMFLOW_MODEL_STAGE_TIMEOUT_SECONDS must be a positive integer") from exc
    if timeout <= 0:
        raise ValueError("TEAMFLOW_MODEL_STAGE_TIMEOUT_SECONDS must be a positive integer")
    return timeout


def configured_model_stage_timeout() -> int | None:
    try:
        value = os.environ.get("TEAMFLOW_MODEL_STAGE_TIMEOUT_SECONDS")
        return parse_model_stage_timeout(value)
    except ValueError as exc:
        fail(str(exc))


def run_model(command: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    timeout_seconds = configured_model_stage_timeout()
    process = subprocess.Popen(
        command,
        cwd=cwd,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=os.environ.copy(),
        start_new_session=True,
    )
    try:
        if timeout_seconds is None:
            stdout, stderr = process.communicate()
        else:
            stdout, stderr = process.communicate(timeout=timeout_seconds)
        return subprocess.CompletedProcess(command, process.returncode, stdout, stderr)
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGTERM)
        try:
            stdout, stderr = process.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            stdout, stderr = process.communicate()
        stderr += f"\nteamflow model stage timed out after {timeout_seconds}s\n"
        return subprocess.CompletedProcess(command, 124, stdout, stderr)


def validate_capture_receipt(value: object) -> list[str]:
    if not isinstance(value, dict):
        return ["capture receipt must be an object"]
    errors: list[str] = []
    if value.get("schema_version") != 1 or value.get("kind") != "verified-task-receipt":
        errors.append("capture receipt schema_version/kind is invalid")
    if value.get("outcome") != "PASS":
        errors.append("capture receipt outcome must be PASS")
    for field in ("task", "summary"):
        if not isinstance(value.get(field), str) or not value.get(field, "").strip():
            errors.append(f"capture receipt {field} is required")
    if not isinstance(value.get("evidence"), list) or not value.get("evidence"):
        errors.append("capture receipt evidence must be a non-empty array")
    elif not all(isinstance(item, dict) and item.get("status") == "PASS" for item in value["evidence"]):
        errors.append("every capture receipt evidence item must have status PASS")
    for field in ("changed_files", "facts", "decisions", "constraints", "risks", "user_signals", "related_memory"):
        if field in value and not isinstance(value[field], list):
            errors.append(f"capture receipt {field} must be an array")
    if SECRET_RE.search(json.dumps(value, ensure_ascii=False)):
        errors.append("capture receipt appears to contain a secret")
    return errors


def candidate_text(item: dict) -> str:
    content = item.get("content")
    if isinstance(content, str) and content.strip():
        return content.strip()
    statement = item.get("statement")
    if isinstance(statement, str) and statement.strip():
        return statement.strip()
    parts = [
        str(item.get(key, "")).strip()
        for key in ("subject", "predicate", "object")
        if isinstance(item.get(key), str) and item.get(key).strip()
    ]
    if len(parts) == 3:
        return " ".join(parts)
    return ""


def apply_candidates(project_root: Path, run_dir: Path, formatting: dict, repository_slug: str) -> dict:
    report = {
        "schema_version": 1,
        "applied": [],
        "deferred": [],
        "skipped": [],
        "source_disposition": formatting.get("source_disposition", []),
    }
    teamflow_home = Path(os.environ.get("TEAMFLOW_HOME", str(Path.home() / ".teamflow"))).expanduser()
    memory_root = Path(
        os.environ.get("TEAMFLOW_MEMORY_HOME", str(teamflow_home / "memory"))
    ).expanduser()
    memory_env = os.environ.copy()
    memory_env["BASIC_MEMORY_AUTO_UPDATE"] = "false"
    memory_env["BASIC_MEMORY_CONFIG_DIR"] = str(memory_root / "state")
    memory_env["BASIC_MEMORY_HOME"] = str(memory_root / "knowledge")
    memory_env["BASIC_MEMORY_SEMANTIC_SEARCH_ENABLED"] = "false"
    memory_project = os.environ.get(
        "TEAMFLOW_MEMORY_PROJECT", os.environ.get("BASIC_MEMORY_PROJECT", "teamflow")
    )
    capsule_path = run_dir / "00-evidence-capsule.json"
    if capsule_path.is_file():
        capsule = json.loads(capsule_path.read_text(encoding="utf-8"))
        source_paths = {
            item.get("id"): project_root / item.get("path", "")
            for item in capsule.get("sources", []) if isinstance(item, dict)
        }
        atomic_sources = set()
        for source_id, source_path in source_paths.items():
            if source_path.is_file():
                head = "\n".join(source_path.read_text(encoding="utf-8").splitlines()[:20])
                if re.search(r"(?m)^type:\s*(?:teamflow|workflow)_memory\s*$", head):
                    atomic_sources.add(source_id)
        invalid_dispositions = [
            item.get("source_id") for item in formatting.get("source_disposition", [])
            if isinstance(item, dict)
            and item.get("source_id") in atomic_sources
            and item.get("action") != "retain"
        ]
        if invalid_dispositions:
            fail(f"refusing apply: formatter attempted to replace atomic memory sources {sorted(invalid_dispositions)}")
    actionable_creates = [
        item for item in formatting.get("candidates", [])
        if isinstance(item, dict)
        and item.get("action") == "create"
        and item.get("scope") != "cross-project"
        and candidate_text(item)
        and not SECRET_RE.search(candidate_text(item))
    ]
    if len(actionable_creates) > MAX_CREATES_PER_RUN:
        fail(
            "refusing apply: formatter proposed "
            f"{len(actionable_creates)} creates; limit is {MAX_CREATES_PER_RUN}"
        )
    for item in formatting.get("candidates", []):
        if not isinstance(item, dict):
            continue
        candidate_id = item.get("id", "unknown")
        action = item.get("action")
        if action == "skip":
            report["skipped"].append({"id": candidate_id, "reason": item.get("action_reason", "model proposed skip")})
            continue
        if action in {"update", "supersede"}:
            report["deferred"].append({
                "id": candidate_id,
                "action": action,
                "reason": "non-destructive policy records update/supersede proposals without overwriting existing notes",
                "supersedes": item.get("supersedes", []),
            })
            continue
        if action != "create":
            report["deferred"].append({"id": candidate_id, "action": action, "reason": "unsupported apply action"})
            continue
        if item.get("scope") == "cross-project":
            report["deferred"].append({"id": candidate_id, "action": action, "reason": "cross-project promotion requires evidence from more than one repository"})
            continue
        semantic = candidate_text(item)
        if not semantic or SECRET_RE.search(semantic):
            report["deferred"].append({"id": candidate_id, "action": action, "reason": "empty or sensitive candidate"})
            continue
        scope = item.get("scope", "repository")
        digest_input = json.dumps({
            "scope": scope,
            "type": item.get("type"),
            "semantic": " ".join(semantic.split()).casefold(),
        }, ensure_ascii=False, sort_keys=True)
        digest = hashlib.sha256(digest_input.encode("utf-8")).hexdigest()[:12]
        prefix = re.sub(r"\s+", " ", semantic).strip()[:52].rstrip(" .,:;")
        title = f"{prefix} [{digest}]"
        folder = "global/curated" if scope == "cross-project" else f"projects/{repository_slug}/curated"
        content = (
            f"# {title}\n\n{semantic}\n\n## Observations\n\n"
            f"- [type] {item.get('type', 'fact')}\n"
            f"- [status] {item.get('status', 'verified')}\n"
            f"- [scope] {scope}\n"
            f"- [evidence] {', '.join(item.get('evidence_refs', []))}\n"
            f"- [lineage] {', '.join(item.get('derived_from', []))}\n"
        )
        result = subprocess.run(
            [
                "basic-memory", "tool", "write-note", "--title", title, "--folder", folder,
                "--content", content, "--tags", f"teamflow,curated,{item.get('type', 'fact')}",
                "--type", "teamflow-memory", "--project", memory_project,
                "--overwrite", "--local",
            ],
            cwd=project_root,
            text=True,
            capture_output=True,
            env=memory_env,
        )
        if result.returncode != 0:
            fail(f"could not apply candidate {candidate_id}: {result.stderr.strip()}")
        report["applied"].append({"id": candidate_id, "title": title, "folder": folder, "digest": digest})
    write_json(run_dir / "50-apply.json", report)
    return report


def parse_json_output(text: str, label: str) -> dict:
    start = text.find("{")
    if start < 0:
        fail(f"{label} returned no JSON object")
    try:
        value = json.loads(text[start:])
    except json.JSONDecodeError as exc:
        fail(f"{label} returned invalid JSON: {exc}")
    if not isinstance(value, dict):
        fail(f"{label} must return a JSON object")
    return value


def validate_emotion(value: object, source_ids: set[str]) -> list[str]:
    if not isinstance(value, dict):
        return ["emotion output must be a JSON object"]
    if value.get("schema_version") != 1 or not isinstance(value.get("predictions"), list):
        return ["invalid emotion output schema"]
    errors: list[str] = []
    predictions = value["predictions"]
    ids = [item.get("id") for item in predictions if isinstance(item, dict)]
    if set(ids) != source_ids or len(ids) != len(source_ids):
        errors.append("emotion predictions must cover each memory source exactly once")
    for item in predictions:
        if not isinstance(item, dict):
            errors.append("emotion prediction must be an object")
            continue
        labels = item.get("labels")
        if (
            not isinstance(labels, list)
            or not all(isinstance(label, str) for label in labels)
            or not set(labels).issubset(EMOTION_LABELS)
        ):
            errors.append(f"{item.get('id')}: invalid emotion labels")
        elif "neutral" in labels and len(labels) > 1:
            errors.append(f"{item.get('id')}: neutral cannot coexist with other labels")
        for field in ("intensity", "memory_salience"):
            if item.get(field) not in (0, 1, 2, 3):
                errors.append(f"{item.get('id')}: {field} must be 0..3")
        expected_durable = item.get("memory_salience") in (2, 3)
        if item.get("durable_candidate") is not expected_durable:
            errors.append(f"{item.get('id')}: durable_candidate must equal memory_salience >= 2")
        if item.get("recommended_action") not in EMOTION_ACTIONS:
            errors.append(f"{item.get('id')}: invalid recommended_action")
        if not isinstance(item.get("target_topic"), str) or not item.get("target_topic", "").strip():
            errors.append(f"{item.get('id')}: target_topic is required")
    return errors


def validate_stage(
    stage: str,
    value: dict,
    source_ids: set[str],
    note_source_ids: set[str],
    prior: dict[str, dict],
    source_text: str,
) -> list[str]:
    errors: list[str] = []
    if value.get("schema_version") != 1:
        errors.append("schema_version must be 1")
    if value.get("stage") != stage:
        errors.append(f"stage must be {stage!r}")
    required = {
        "compression": ["source_ids", "claims", "evidence", "excluded", "conflicts"],
        "extraction": ["concepts", "facts", "decisions", "relations", "procedures", "problems"],
        "formatting": ["candidates", "source_disposition", "excluded", "conflicts"],
    }[stage]
    for key in required:
        if not isinstance(value.get(key), list):
            errors.append(f"{key} must be an array")
    output_text = json.dumps(value, ensure_ascii=False)
    protected_literals = set(PROTECTED_LITERAL_RE.findall(output_text)) | set(DATED_ID_RE.findall(output_text))
    invented_literals = sorted(literal for literal in protected_literals if literal not in source_text)
    if invented_literals:
        errors.append(f"opaque identifiers were not copied from sources: {invented_literals}")
    if stage == "compression":
        unknown = set(value.get("source_ids", [])) - source_ids
        if unknown:
            errors.append(f"unknown source_ids: {sorted(unknown)}")
        ids = [item.get("id") for item in value.get("claims", []) if isinstance(item, dict)]
        if None in ids or len(ids) != len(set(ids)):
            errors.append("claim ids must be present and unique")
        evidence_ids = [item.get("id") for item in value.get("evidence", []) if isinstance(item, dict)]
        if None in evidence_ids or len(evidence_ids) != len(set(evidence_ids)):
            errors.append("evidence ids must be present and unique")
        known_evidence = set(evidence_ids)
        for claim in value.get("claims", []):
            if not isinstance(claim, dict):
                errors.append("each claim must be an object")
                continue
            unknown_sources = set(claim.get("evidence_refs", [])) - source_ids
            unknown_evidence = set(claim.get("evidence_ids", [])) - known_evidence
            if unknown_sources:
                errors.append(f"claim {claim.get('id')} has unknown source refs {sorted(unknown_sources)}")
            if unknown_evidence:
                errors.append(f"claim {claim.get('id')} has unknown evidence ids {sorted(unknown_evidence)}")
        for evidence in value.get("evidence", []):
            if isinstance(evidence, dict):
                unknown_sources = set(evidence.get("source_refs", [])) - source_ids
                if unknown_sources:
                    errors.append(f"evidence {evidence.get('id')} has unknown source refs {sorted(unknown_sources)}")
    if stage == "extraction":
        compression = prior.get("compression", {})
        claim_ids = {item.get("id") for item in compression.get("claims", []) if isinstance(item, dict)}
        evidence_ids = {item.get("id") for item in compression.get("evidence", []) if isinstance(item, dict)}
        concept_ids = {item.get("id") for item in value.get("concepts", []) if isinstance(item, dict)}
        all_ids: list[str] = []
        for key in ("concepts", "facts", "decisions", "relations", "procedures", "problems"):
            all_ids.extend(item.get("id") for item in value.get(key, []) if isinstance(item, dict))
        if None in all_ids or len(all_ids) != len(set(all_ids)):
            errors.append("extraction item ids must be present and globally unique")
        for fact in value.get("facts", []):
            if isinstance(fact, dict) and fact.get("subject") not in concept_ids:
                errors.append(f"fact {fact.get('id')} references unknown concept {fact.get('subject')}")
        for key in ("concepts", "facts", "decisions", "relations", "procedures", "problems"):
            for item in value.get(key, []):
                if not isinstance(item, dict):
                    continue
                if not item.get("derived_from"):
                    errors.append(f"{key} item {item.get('id')} is missing derived_from")
                elif set(item.get("derived_from", [])) - claim_ids:
                    errors.append(f"{key} item {item.get('id')} has unknown claim lineage")
                if item.get("evidence_ids") and set(item.get("evidence_ids", [])) - evidence_ids:
                    errors.append(f"{key} item {item.get('id')} has unknown evidence ids")
        for relation in value.get("relations", []):
            if not isinstance(relation, dict):
                continue
            if relation.get("from") not in concept_ids or relation.get("to") not in concept_ids:
                errors.append(f"relation {relation.get('id')} endpoints must reference concepts")
    if stage == "formatting":
        extraction = prior.get("extraction", {})
        compression = prior.get("compression", {})
        extraction_ids = {
            item.get("id")
            for key in ("concepts", "facts", "decisions", "relations", "procedures", "problems")
            for item in extraction.get(key, [])
            if isinstance(item, dict)
        }
        evidence_ids = {item.get("id") for item in compression.get("evidence", []) if isinstance(item, dict)}
        ids = [item.get("id") for item in value.get("candidates", []) if isinstance(item, dict)]
        if None in ids or len(ids) != len(set(ids)):
            errors.append("candidate ids must be present and unique")
        create_count = sum(
            isinstance(item, dict) and item.get("action") == "create"
            for item in value.get("candidates", [])
        )
        if create_count > MAX_CREATES_PER_RUN:
            errors.append(
                f"formatting may propose at most {MAX_CREATES_PER_RUN} create candidates; got {create_count}"
            )
        allowed_actions = {"create", "update", "supersede", "skip"}
        for item in value.get("candidates", []):
            if not isinstance(item, dict):
                continue
            if item.get("action") not in allowed_actions:
                errors.append(f"candidate {item.get('id')} has invalid action")
            if not item.get("action_reason"):
                errors.append(f"candidate {item.get('id')} is missing action_reason")
            if not item.get("derived_from"):
                errors.append(f"candidate {item.get('id')} is missing derived_from")
            elif set(item.get("derived_from", [])) - extraction_ids:
                errors.append(f"candidate {item.get('id')} has unknown extraction lineage")
            if not item.get("evidence_refs"):
                errors.append(f"candidate {item.get('id')} is missing evidence_refs")
            if set(item.get("evidence_refs", [])) - source_ids:
                errors.append(f"candidate {item.get('id')} has unknown evidence_refs")
            if item.get("evidence_ids") and set(item.get("evidence_ids", [])) - evidence_ids:
                errors.append(f"candidate {item.get('id')} has unknown evidence ids")
            if item.get("action") in {"create", "update", "supersede"} and not candidate_text(item):
                errors.append(
                    f"candidate {item.get('id')} has no canonical semantic text "
                    "(provide non-empty content, a legacy statement, or a complete subject-predicate-object triple; subject alone is invalid)"
                )
        concept_candidate_subjects = {
            item.get("subject")
            for item in value.get("candidates", [])
            if isinstance(item, dict) and item.get("type") == "concept"
        }
        referenced_concepts: set[str] = set()
        extraction_concepts = {
            item.get("id") for item in extraction.get("concepts", []) if isinstance(item, dict)
        }
        for item in value.get("candidates", []):
            if not isinstance(item, dict) or item.get("type") == "concept":
                continue
            if item.get("subject") in extraction_concepts:
                referenced_concepts.add(item.get("subject"))
            if item.get("type") == "relation" and item.get("object") in extraction_concepts:
                referenced_concepts.add(item.get("object"))
        missing_concepts = sorted(referenced_concepts - concept_candidate_subjects)
        if missing_concepts:
            errors.append(f"referenced concepts are missing concept candidates: {missing_concepts}")
        disposition_ids = {item.get("source_id") for item in value.get("source_disposition", []) if isinstance(item, dict)}
        if not note_source_ids.issubset(disposition_ids) or disposition_ids - source_ids:
            errors.append("source_disposition must cover all memory notes and may reference only capsule sources")
        for disposition in value.get("source_disposition", []):
            if not isinstance(disposition, dict):
                continue
            if disposition.get("source_id") not in note_source_ids and disposition.get("action") not in {"retain", "review"}:
                errors.append(f"non-note source {disposition.get('source_id')} cannot be superseded")
    return errors


def resume_formatting(project_root: Path, runtime: Path, teamflow: Path, run_id: str, apply: bool) -> None:
    run_dir = runtime / "runs" / "memory" / run_id
    required = ["00-evidence-capsule.json", "10-compressed.json", "20-extracted.json"]
    if not run_dir.is_dir() or any(not (run_dir / name).is_file() for name in required):
        fail("resume formatting run is missing required upstream artifacts")
    capsule = json.loads((run_dir / "00-evidence-capsule.json").read_text(encoding="utf-8"))
    compression = json.loads((run_dir / "10-compressed.json").read_text(encoding="utf-8"))
    extraction = json.loads((run_dir / "20-extracted.json").read_text(encoding="utf-8"))
    sources = capsule.get("sources", [])
    source_ids = {item.get("id") for item in sources if isinstance(item, dict)}
    note_source_ids = {item.get("id") for item in sources if isinstance(item, dict) and item.get("type") == "memory-note"}
    bodies = []
    for source in sources:
        path = project_root / source.get("path", "")
        if path.is_file():
            bodies.append(path.read_text(encoding="utf-8"))
    output_path = run_dir / "30-candidates.json"
    prompt = (
        "Load extract-memory and perform only the formatting stage. Read "
        f"{(run_dir / '20-extracted.json').relative_to(project_root)}, "
        f"{(run_dir / '10-compressed.json').relative_to(project_root)}, and "
        f"{(run_dir / '00-evidence-capsule.json').relative_to(project_root)}. Rewrite strict JSON to "
        f"{output_path.relative_to(project_root)}. For every concept candidate, subject and derived_from "
        "must contain the exact extraction concept ID, never compression claim IDs. Do not read other files, "
        "invent semantics, write Basic Memory, or include Markdown fences."
    )
    result = run_model([str(teamflow), "run", "--agent", "memory-formatter", prompt], project_root)
    if result.returncode != 0 or not output_path.is_file():
        fail("resumed formatting agent failed; see manifest receipt")
    value = json.loads(output_path.read_text(encoding="utf-8"))
    errors = validate_stage(
        "formatting", value, source_ids, note_source_ids,
        {"compression": compression, "extraction": extraction}, "\n".join(bodies),
    )
    repair_attempted = False
    if errors:
        repair_attempted = True
        repair_prompt = (
            "Load extract-memory and perform only the formatting stage. Read "
            f"{(run_dir / '20-extracted.json').relative_to(project_root)}, "
            f"{(run_dir / '10-compressed.json').relative_to(project_root)}, and "
            f"{(run_dir / '00-evidence-capsule.json').relative_to(project_root)}. Your existing output at "
            f"{output_path.relative_to(project_root)} failed deterministic validation with these exact errors: "
            f"{json.dumps(errors, ensure_ascii=False)}. Rewrite the complete strict JSON output, preserving the "
            "current 3–5 semantic groups while correcting only the reported schema, lineage, evidence, concept, "
            "source_disposition, excluded, and conflicts contract violations. Do not add new semantic targets, "
            "read unrelated files, write Basic Memory, or include Markdown fences."
        )
        repair_result = run_model(
            [str(teamflow), "run", "--agent", "memory-formatter", repair_prompt], project_root
        )
        if repair_result.returncode != 0 or not output_path.is_file():
            fail("resumed formatting repair failed; see manifest receipt")
        try:
            value = json.loads(output_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            fail(f"repaired {output_path.name} is invalid JSON: {exc}")
        errors = validate_stage(
            "formatting", value, source_ids, note_source_ids,
            {"compression": compression, "extraction": extraction}, "\n".join(bodies),
        )
    validation_path = run_dir / "40-validation.json"
    validation = json.loads(validation_path.read_text(encoding="utf-8")) if validation_path.is_file() else {
        "schema_version": 1,
        "run_id": run_id,
        "passed": False,
        "stages": {
            "emotion_detection": {"passed": (run_dir / "06-emotion-signals.json").is_file(), "resumed_from_artifact": True},
            "compression": {"passed": True, "resumed_from_artifact": True},
            "extraction": {"passed": True, "resumed_from_artifact": True},
        },
    }
    validation["stages"]["formatting"] = {
        "passed": not errors,
        "errors": errors,
        "contract_resume": True,
        "repair_attempted": repair_attempted,
    }
    validation["passed"] = not errors and all(
        stage.get("passed") is True for stage in validation["stages"].values()
    )
    write_json(validation_path, validation)
    if errors:
        fail(f"resumed formatting validation failed: {'; '.join(errors)}")
    if apply:
        repository = subprocess.run(
            ["git", "config", "--get", "remote.origin.url"], cwd=project_root, text=True, capture_output=True
        ).stdout.strip() or project_root.name
        repository_slug = re.sub(r"[^a-z0-9._-]+", "-", repository.rsplit("/", 1)[-1].removesuffix(".git").lower()).strip("-")
        apply_candidates(project_root, run_dir, value, repository_slug or project_root.name.lower())
    print(json.dumps({"status": "PASS", "run_id": run_id, "validation": "40-validation.json", "apply": "50-apply.json" if apply else None}, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the complete four-stage memory candidate pipeline")
    parser.add_argument("--source", action="append", default=[], help="Basic Memory URL or permalink; repeat for multiple notes")
    parser.add_argument("--evidence-file", action="append", default=[], help="Current verified evidence JSON; repeat as needed")
    parser.add_argument("--capture-file", help="Verified task receipt below .teamflow/runs/task-receipts/")
    parser.add_argument("--apply", action="store_true", help="Apply safe create candidates after validation")
    parser.add_argument("--resume-apply", help="Apply an already validated run without rerunning models")
    parser.add_argument("--resume-formatting", help="Rerun formatting for an existing run after a contract correction")
    parser.add_argument("--run-id", help="Optional stable run id")
    args = parser.parse_args()

    project_root = Path.cwd().resolve()
    runtime = project_root / ".teamflow"
    teamflow = runtime / "bin" / "teamflow"
    if not teamflow.is_file():
        fail("run inside a project initialized with teamflow")
    if args.resume_formatting:
        resume_formatting(project_root, runtime, teamflow, args.resume_formatting, args.apply)
        return
    if args.resume_apply:
        run_dir = runtime / "runs" / "memory" / args.resume_apply
        validation_path = run_dir / "40-validation.json"
        formatting_path = run_dir / "30-candidates.json"
        if not validation_path.is_file() or not formatting_path.is_file():
            fail("resume run is missing validation or candidates")
        validation_value = json.loads(validation_path.read_text(encoding="utf-8"))
        if validation_value.get("passed") is not True:
            fail("resume run did not pass deterministic validation")
        formatting_value = json.loads(formatting_path.read_text(encoding="utf-8"))
        repository = subprocess.run(
            ["git", "config", "--get", "remote.origin.url"], cwd=project_root, text=True, capture_output=True
        ).stdout.strip() or project_root.name
        repository_slug = re.sub(r"[^a-z0-9._-]+", "-", repository.rsplit("/", 1)[-1].removesuffix(".git").lower()).strip("-")
        apply_candidates(project_root, run_dir, formatting_value, repository_slug or project_root.name.lower())
        print(json.dumps({"status": "PASS", "run_id": args.resume_apply, "apply": "50-apply.json"}, indent=2))
        return
    if not args.source and not args.evidence_file and not args.capture_file:
        fail("provide at least one --source, --evidence-file, or --capture-file")
    if args.apply and not args.capture_file:
        fail("--apply requires --capture-file")

    run_id = args.run_id or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_dir = runtime / "runs" / "memory" / run_id
    if run_dir.exists():
        fail(f"run directory already exists: {run_dir}")
    run_dir.mkdir(parents=True)
    source_dir = run_dir / "sources"
    source_dir.mkdir()

    sources = []
    source_bodies: list[str] = []
    emotion_items: list[dict[str, str]] = []
    for index, source in enumerate(args.source, start=1):
        result = subprocess.run(
            [str(teamflow), "memory", "read", source],
            cwd=project_root,
            text=True,
            capture_output=True,
        )
        if result.returncode != 0:
            fail(f"could not read {source}: {result.stderr.strip()}")
        note = parse_json_output(result.stdout, source)
        content = note.get("content", "")
        source_id = f"NOTE-{index}"
        source_path = source_dir / f"{source_id}.md"
        source_path.write_text(content, encoding="utf-8")
        source_bodies.append(content)
        emotion_items.append({"id": source_id, "text": content})
        sources.append({
            "id": source_id,
            "type": "memory-note",
            "permalink": note.get("permalink", source),
            "title": note.get("title", ""),
            "sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
            "path": str(source_path.relative_to(project_root)),
        })

    for index, evidence_file in enumerate(args.evidence_file, start=1):
        path = Path(evidence_file).expanduser().resolve()
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            fail(f"could not read evidence file {path}: {exc}")
        content = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        source_id = f"EVIDENCE-{index}"
        source_path = source_dir / f"{source_id}.json"
        write_json(source_path, payload)
        source_bodies.append(content)
        sources.append({
            "id": source_id,
            "type": "verification-receipt",
            "title": payload.get("title", path.stem) if isinstance(payload, dict) else path.stem,
            "sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
            "path": str(source_path.relative_to(project_root)),
        })
        emotion_items.append({"id": source_id, "text": content})

    capture_value = None
    if args.capture_file:
        path = Path(args.capture_file).expanduser().resolve()
        allowed = (project_root / ".teamflow" / "runs" / "task-receipts").resolve()
        if not path.is_file() or allowed not in path.parents:
            fail("capture file must be below .teamflow/runs/task-receipts/")
        try:
            capture_value = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            fail(f"could not read capture file {path}: {exc}")
        receipt_errors = validate_capture_receipt(capture_value)
        if receipt_errors:
            fail(f"invalid capture receipt: {'; '.join(receipt_errors)}")
        content = json.dumps(capture_value, ensure_ascii=False, sort_keys=True)
        source_id = "RECEIPT-1"
        source_path = source_dir / f"{source_id}.json"
        write_json(source_path, capture_value)
        source_bodies.append(content)
        sources.append({
            "id": source_id,
            "type": "verification-receipt",
            "title": capture_value["task"],
            "sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
            "path": str(source_path.relative_to(project_root)),
        })
        signal_texts = [
            item.get("text", "") if isinstance(item, dict) else str(item)
            for item in capture_value.get("user_signals", [])
        ]
        emotion_items.append({"id": source_id, "text": "\n".join(filter(None, signal_texts)) or capture_value["summary"]})

    capsule = {"schema_version": 1, "kind": "evidence-capsule", "sources": sources}
    write_json(run_dir / "00-evidence-capsule.json", capsule)
    write_json(run_dir / "05-emotion-input.json", {"schema_version": 1, "items": emotion_items})
    manifest = {
        "schema_version": 1,
        "run_id": run_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "write_basic_memory": args.apply,
        "models": {
            "emotion_detection": "mimo/mimo-v2.5-pro",
            "compression": "deepseek/deepseek-v4-pro",
            "extraction": "zhipuai-coding-plan/glm-5.2",
            "formatting": "zhipuai-coding-plan/glm-5.2",
        },
        "sources": [
            {
                "id": source["id"],
                "type": source.get("type", "unknown"),
                "reference": source.get("permalink", source.get("title", "")),
                "sha256": source["sha256"],
            }
            for source in sources
        ],
        "stages": {},
    }
    write_json(run_dir / "manifest.json", manifest)

    source_ids = {source["id"] for source in sources}
    note_source_ids = {source["id"] for source in sources if source.get("type") == "memory-note"}
    source_text = "\n".join(source_bodies)
    validation = {"schema_version": 1, "run_id": run_id, "passed": True, "stages": {}}

    emotion_input = run_dir / "05-emotion-input.json"
    emotion_output = run_dir / "06-emotion-signals.json"
    emotion_prompt = (
        "Load detect-emotional-salience and follow its contract. Read "
        f"{emotion_input.relative_to(project_root)} and write strict contract JSON to "
        f"{emotion_output.relative_to(project_root)}. Treat each item id as an opaque source id. "
        "Do not read other files, ask the user questions, diagnose psychology, or write memory."
    )
    emotion_result = run_model(
        [str(teamflow), "run", "--agent", "emotional-salience-sensor", emotion_prompt], project_root
    )
    manifest["stages"]["emotion_detection"] = {
        "agent": "emotional-salience-sensor",
        "exit_code": emotion_result.returncode,
        "output": emotion_output.name,
    }
    write_json(run_dir / "manifest.json", manifest)
    if emotion_result.returncode != 0 or not emotion_output.is_file():
        manifest["stages"]["emotion_detection"]["failure_kind"] = (
            "timeout" if emotion_result.returncode == 124 else
            "agent_exit" if emotion_result.returncode != 0 else "missing_output"
        )
        write_json(run_dir / "manifest.json", manifest)
        fail("emotion detection failed; see manifest receipt")
    try:
        emotion_value = json.loads(emotion_output.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        manifest["stages"]["emotion_detection"]["failure_kind"] = "invalid_json"
        write_json(run_dir / "manifest.json", manifest)
        fail(f"{emotion_output.name} is invalid JSON: {exc}")
    emotion_errors = validate_emotion(emotion_value, source_ids)
    if emotion_errors:
        repair_prompt = (
            "Load detect-emotional-salience and follow its contract. Read "
            f"{emotion_input.relative_to(project_root)} and rewrite strict contract JSON to "
            f"{emotion_output.relative_to(project_root)}. The previous output failed deterministic validation: "
            f"{json.dumps(emotion_errors, ensure_ascii=False)}. Correct only those schema violations. "
            "Use neutral only when no other label applies; never combine neutral with another label. "
            "Do not read other files, ask questions, diagnose psychology, or write memory."
        )
        repair_result = run_model(
            [str(teamflow), "run", "--agent", "emotional-salience-sensor", repair_prompt], project_root
        )
        manifest["stages"]["emotion_detection"]["repair_exit_code"] = repair_result.returncode
        write_json(run_dir / "manifest.json", manifest)
        if repair_result.returncode != 0 or not emotion_output.is_file():
            manifest["stages"]["emotion_detection"]["repair_status"] = "failed"
            manifest["stages"]["emotion_detection"]["failure_kind"] = (
                "timeout" if repair_result.returncode == 124 else
                "agent_exit" if repair_result.returncode != 0 else "missing_output"
            )
            write_json(run_dir / "manifest.json", manifest)
            fail("emotion detection repair failed; see manifest receipt")
        try:
            emotion_value = json.loads(emotion_output.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            manifest["stages"]["emotion_detection"]["repair_status"] = "failed"
            manifest["stages"]["emotion_detection"]["failure_kind"] = "invalid_json"
            write_json(run_dir / "manifest.json", manifest)
            fail(f"repaired {emotion_output.name} is invalid JSON: {exc}")
        emotion_errors = validate_emotion(emotion_value, source_ids)
        manifest["stages"]["emotion_detection"]["repair_status"] = (
            "passed" if not emotion_errors else "failed"
        )
        write_json(run_dir / "manifest.json", manifest)
    validation["stages"]["emotion_detection"] = {
        "passed": not emotion_errors,
        "errors": emotion_errors,
        "repair_attempted": "repair_exit_code" in manifest["stages"]["emotion_detection"],
    }
    if emotion_errors:
        manifest["stages"]["emotion_detection"]["failure_kind"] = "contract_violation"
        write_json(run_dir / "manifest.json", manifest)
        validation["passed"] = False
        write_json(run_dir / "40-validation.json", validation)
        fail(f"emotion detection validation failed: {'; '.join(emotion_errors)}")

    prior: dict[str, dict] = {}
    for stage, agent, input_name, output_name in STAGES:
        input_path = run_dir / input_name
        output_path = run_dir / output_name
        extra_inputs = ""
        if stage == "compression":
            extra_inputs = (
                " Read every source file listed in the capsule's sources[].path and also read "
                f"{emotion_output.relative_to(project_root)} before writing the compression artifact. "
                "Use emotional salience only as attention metadata: preserve its target or record why it is excluded, "
                "but never treat emotion as evidence or promote a claim solely because its intensity is high."
            )
        if stage == "formatting":
            extra_inputs = (
                f" Also read {(run_dir / '10-compressed.json').relative_to(project_root)} for evidence lineage "
                f"and {(run_dir / '00-evidence-capsule.json').relative_to(project_root)} for existing-note and current-receipt comparison."
            )
        prompt = (
            f"Load extract-memory and perform only the {stage} stage. "
            f"Read {input_path.relative_to(project_root)} and write strict JSON to "
            f"{output_path.relative_to(project_root)}.{extra_inputs} Do not read other repository files, "
            "do not write Basic Memory, and do not include Markdown fences."
        )
        result = run_model([str(teamflow), "run", "--agent", agent, prompt], project_root)
        manifest["stages"][stage] = {"agent": agent, "exit_code": result.returncode, "output": output_name}
        write_json(run_dir / "manifest.json", manifest)
        if result.returncode != 0:
            manifest["stages"][stage]["failure_kind"] = (
                "timeout" if result.returncode == 124 else "agent_exit"
            )
            write_json(run_dir / "manifest.json", manifest)
            fail(f"{stage} agent failed; see manifest receipt")
        if not output_path.is_file():
            manifest["stages"][stage]["failure_kind"] = "missing_output"
            write_json(run_dir / "manifest.json", manifest)
            fail(f"{stage} agent did not create {output_path}")
        try:
            value = json.loads(output_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            fail(f"{output_name} is invalid JSON: {exc}")
        errors = validate_stage(stage, value, source_ids, note_source_ids, prior, source_text)
        if errors:
            repair_prompt = (
                f"Load extract-memory and perform only the {stage} stage. Your previous output at "
                f"{output_path.relative_to(project_root)} failed deterministic validation with these errors: "
                f"{json.dumps(errors, ensure_ascii=False)}. Read {input_path.relative_to(project_root)} and rewrite the full "
                "strict JSON output, correcting only the reported contract violations. Do not invent semantics, "
                "read other repository files, write Basic Memory, or include Markdown fences."
                f"{extra_inputs}"
            )
            repair_result = run_model(
                [str(teamflow), "run", "--agent", agent, repair_prompt], project_root
            )
            manifest["stages"][stage]["repair_exit_code"] = repair_result.returncode
            write_json(run_dir / "manifest.json", manifest)
            if repair_result.returncode != 0 or not output_path.is_file():
                manifest["stages"][stage]["repair_status"] = "failed"
                manifest["stages"][stage]["failure_kind"] = (
                    "timeout" if repair_result.returncode == 124 else
                    "agent_exit" if repair_result.returncode != 0 else "missing_output"
                )
                write_json(run_dir / "manifest.json", manifest)
                fail(f"{stage} repair failed; see manifest receipt")
            try:
                value = json.loads(output_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError as exc:
                fail(f"repaired {output_name} is invalid JSON: {exc}")
            errors = validate_stage(stage, value, source_ids, note_source_ids, prior, source_text)
            manifest["stages"][stage]["repair_status"] = "passed" if not errors else "failed"
            write_json(run_dir / "manifest.json", manifest)
        validation["stages"][stage] = {
            "passed": not errors,
            "errors": errors,
            "repair_attempted": "repair_exit_code" in manifest["stages"][stage],
        }
        if errors:
            manifest["stages"][stage]["failure_kind"] = "contract_violation"
            write_json(run_dir / "manifest.json", manifest)
            validation["passed"] = False
            write_json(run_dir / "40-validation.json", validation)
            fail(f"{stage} validation failed after one repair: {'; '.join(errors)}")
        prior[stage] = value

    write_json(run_dir / "40-validation.json", validation)
    apply_report = None
    if args.apply:
        repository = subprocess.run(
            ["git", "config", "--get", "remote.origin.url"], cwd=project_root, text=True, capture_output=True
        ).stdout.strip() or project_root.name
        repository_slug = re.sub(r"[^a-z0-9._-]+", "-", repository.rsplit("/", 1)[-1].removesuffix(".git").lower()).strip("-")
        apply_report = apply_candidates(project_root, run_dir, prior["formatting"], repository_slug or project_root.name.lower())
    print(json.dumps({
        "status": "PASS", "run_id": run_id, "run_dir": str(run_dir),
        "validation": "40-validation.json", "apply": "50-apply.json" if apply_report is not None else None,
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
