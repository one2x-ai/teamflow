# Memory extraction contracts

## Compression

Inputs: `00-evidence-capsule.json`, the source files listed in `sources[].path`, and `06-emotion-signals.json`. Memory notes are stored as multiline Markdown files to avoid long JSON-line truncation; verification receipts remain JSON. Emotional signals are attention metadata only, never factual evidence.

Output: `10-compressed.json`.

```json
{
  "schema_version": 1,
  "stage": "compression",
  "source_ids": ["NOTE-1"],
  "claims": [
    {
      "id": "claim-001",
      "text": "One durable claim.",
      "evidence_refs": ["NOTE-1"],
      "evidence_ids": ["evidence-001"],
      "uncertainty": null
    }
  ],
  "evidence": [
    {
      "id": "evidence-001",
      "kind": "test|code|config|documentation|human",
      "summary": "Concise proof, not a full log.",
      "source_refs": ["NOTE-1"]
    }
  ],
  "excluded": [{"item": "...", "reason": "...", "source_refs": ["NOTE-1"]}],
  "conflicts": [{"description": "...", "source_refs": ["NOTE-1", "NOTE-2"]}]
}
```

Do not classify concepts yet. A durable claim must change a future engineering decision. Test counts, completion narration, filenames used only once, and command transcripts are evidence or exclusions, not claims. Copy opaque identifiers byte-for-byte. Do not discard authoritative references, applicability constraints, deprecation information, or unresolved disagreement. A newer receipt that invalidates an old statement must appear in `conflicts`.

## Extraction

Input: `10-compressed.json`.

Output: `20-extracted.json`.

```json
{
  "schema_version": 1,
  "stage": "extraction",
  "concepts": [{"id": "concept-id", "name": "...", "definition": "...", "boundary": "...", "derived_from": ["claim-001"]}],
  "facts": [{"id": "fact-001", "subject": "concept-id", "statement": "...", "status": "verified", "derived_from": ["claim-001"], "evidence_ids": ["evidence-001"]}],
  "decisions": [{"id": "decision-001", "subject": "concept-id", "statement": "...", "scope": "repository", "derived_from": ["claim-001"]}],
  "relations": [{"id": "relation-001", "from": "concept-id", "predicate": "...", "to": "other-concept", "derived_from": ["claim-001"]}],
  "procedures": [{"id": "procedure-001", "name": "...", "scope": "...", "steps": ["..."], "derived_from": ["claim-001"]}],
  "problems": [{"id": "problem-001", "type": "...", "description": "...", "derived_from": ["claim-001"]}]
}
```

Use `verified` only when the referenced compressed claim carries explicit evidence IDs and has no unresolved uncertainty. Otherwise use `hypothesis`. Do not add invariants, exclusivity, cardinality, or definitions that are not entailed by compressed claims.

## Formatting

Inputs: `20-extracted.json`, `10-compressed.json` for evidence lineage, and `00-evidence-capsule.json` for existing-note and current-receipt comparison.

Output: `30-candidates.json`.

```json
{
  "schema_version": 1,
  "stage": "formatting",
  "candidates": [
    {
      "id": "candidate-001",
      "type": "concept|fact|decision|relation|procedure",
      "action": "create|update|supersede|skip",
      "subject": "concept-id",
      "predicate": null,
      "object": null,
      "content": "...",
      "statement": "...",
      "scope": "repository|cross-project",
      "status": "verified|hypothesis|disputed",
      "evidence_refs": ["NOTE-1"],
      "evidence_ids": ["evidence-001"],
      "derived_from": ["fact-001"],
      "supersedes": [],
      "retrieval_terms": ["..."],
      "action_reason": "Why this operation follows from the existing memory snapshot."
    }
  ],
  "source_disposition": [
    {
      "source_id": "NOTE-1",
      "action": "retain|supersede|review",
      "reason": "...",
      "replacement_candidates": ["candidate-001"]
    }
  ],
  "excluded": [{"derived_from": "...", "reason": "..."}],
  "conflicts": [{"derived_from": ["..."], "description": "..."}]
}
```

Formatting is normalization, not a new semantic analysis pass. Do not introduce unsupported concepts or claims. Use `skip` when an equivalent or stronger atomic fact already exists, `update` when the same knowledge needs stronger wording or evidence, and `supersede` only for a stale conflicting record. When decomposing a legacy long note, use `source_disposition` to propose replacing it with atomic candidates rather than pretending the existing note is absent.

Read note frontmatter before choosing an action. A source with `type:
teamflow_memory` in a curated folder is already atomic; legacy `workflow_memory`
has the same semantics. Semantically equivalent
knowledge must be `skip`, stronger evidence for the same statement is `update`, and
its source disposition must be `retain`. Never supersede or recreate an atomic note
because wording changed. A source with `type: workflow_finding` is a legacy monolith
and may be proposed for `supersede` after its durable content is covered.

For a concept candidate, set `subject` to the exact extraction concept ID and set
`derived_from` to an array containing that same extraction concept ID. Do not copy the
concept's upstream compression claim IDs into candidate `derived_from`; claim lineage
is already carried by the extraction artifact. For a non-concept candidate,
`derived_from` must contain the corresponding extraction fact, decision, relation,
procedure, or problem ID.

Every `create`, `update`, or `supersede` candidate must carry canonical semantic text. The deterministic runner resolves candidate semantic text with this precedence: non-empty `content` first (canonical), then a non-empty legacy `statement`, then a complete `subject`+`predicate`+`object` triple (all three must be non-empty strings). A bare `subject` or an incomplete triple is invalid — the runner rejects such candidates even when `subject`, `title`, and `action_reason` are present. Prefer `content` for concept and fact candidates; use a complete triple for relation candidates; `statement` is retained only for backward compatibility. `skip` candidates are exempt because they reference existing notes rather than carrying new semantic text.

Every concept referenced by a fact, decision, relation, or procedure must have a corresponding concept candidate, even when that candidate's action is `skip` because an equivalent concept already exists.

`source_disposition` must cover every `memory-note` source. Non-note evidence sources may be included only with `retain` or `review`; they are never superseded as memory notes.
