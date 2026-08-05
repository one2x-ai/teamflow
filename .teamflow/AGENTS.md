# Teamflow Agent Instructions

This working repository uses Teamflow's test-first multi-agent process.

## Roles

- `planner` uses GLM-5.2 and owns requirement analysis, acceptance criteria, delegation, memory recall, and the final report.
- `test-writer` uses GLM-5.2 and owns requirement-focused test design before implementation and assertion/diff review afterward.
- `test-runner` owns test execution and structured error receipts; it never edits files.
- `coder` uses Kimi K3, focuses on the smallest coherent product-code implementation, and must not redefine acceptance criteria.
- `command` uses MiMo 2.5 Pro for explicit shell, Git, and GitHub operations that require no code edits or multi-agent planning.
- `supervisor` uses MiMo 2.5 Pro for deterministic mechanical checks — artifact existence, checksums, and test-patch gates — without editing files or delegating.
- `emotional-salience-sensor`, `memory-compressor`, `memory-extractor`, and `memory-formatter` form the serial capture pipeline; GLM-5.2 owns both extraction and final formatting. Models write only below `.teamflow/runs/memory/`; deterministic apply writes safe new notes and defers update/supersede proposals.

Only depth-0 roles with the strict boolean frontmatter declaration `delegates: true` may receive `task` and `task_group`. Child roles always run at depth 1 and may never delegate further. Roles that declare `needs_project_rules: false` (such as `test-runner` and `command`) skip AGENTS.md injection; they receive only their own system prompt and the handoff message.

## Required sequence

Use this sequence unless the task is documentation-only or cannot be tested:

1. Inspect the current repository and recall relevant shared memory.
2. Verify recalled claims against current files and commands.
3. Define observable acceptance criteria, scope, risks, and non-goals.
4. Ask `test-writer` to create and validate a test-only patch below `.teamflow/runs/test-patches/`.
5. Ask `coder` to apply the validated patch mechanically, then ask `test-runner` to execute it and return a structured failure receipt.
6. Ask `coder` to implement the approved plan without weakening tests.
7. Ask `test-runner` to execute focused and regression checks and return structured receipts.
8. Ask `test-writer` to review assertions and the final diff.
9. Create a verified-task receipt and run `teamflow memory-capture`; never automate capture with direct `memory remember`.
10. Report changed files, execution receipts, memory written, and remaining risks.

If test-first execution is skipped, state the concrete reason.

For command-only requests such as branch creation, committing an already-reviewed diff, pushing, or opening a pull request, use `teamflow command` instead of starting this multi-agent sequence.

## Handoff contract

Coordination happens in handoffs: one unit of work moved from a delegator to a receiver, whose state the receiver maintains until a terminal status. A structured handoff is required for every delegation; author the body with the `write-handoff` skill. Do not hand off vague requests.

State changes and state queries are programmatic; requirement expression and orchestration go through models and prompts. The two planes have a hard boundary:

- `teamflow handoff open/start/finish/status/list` owns every transition (`open` -> `running` -> `done(PASS|FAIL)` or `blocked(reason)`), sequence allocation, receipt schema validation, and event delivery. Delegating through `task`/`task_group` opens the handoff for you.
- Models write handoff bodies, receipt narrative fields, and diagnoses. Never hand-write `state.json`, an event file, an `active/` sentinel, or a liveness record, and never claim liveness in prose. State that depends on an agent remembering to write it is a defect.

A delegated `PASS` or `FAIL` requires a validated receipt file: `teamflow handoff finish --id "$TEAMFLOW_HANDOFF_ID" --status <STATUS> --receipt <file> --summary "<one line>"`. A final assistant message is not a receipt. `BLOCKED` needs no receipt file because the reason enum is the receipt.

`blocked.reason` is one enum: `CONTEXT_BUDGET_EXCEEDED`, `RECALL_BUDGET_EXCEEDED`, `DELEGATION_ARTIFACT_MISSING`, `OUTPUT_TRUNCATED`, `PROVIDER_FAILURE`, `USER_CANCELLED`. Pass `--blocked-reason` more than once when more than one applies.

## Engineering rules

- Preserve existing project instructions and user changes.
- Never expose, print, or commit secrets.
- Never weaken assertions merely to make a test pass.
- Run focused checks before broader lint, typecheck, test, and build gates.
- Do not push, force-reset, or clean the workspace without explicit authorization.
- Put temporary teamflow run artifacts below `.teamflow/runs/`.
- Explicit provider timeout, authentication failure, quota exhaustion, overload, transport failure, and user cancellation finish a handoff `BLOCKED`; they are never implicit retries of the full Teamflow process. There is no local wall-time timeout by default, so silence while a provider queues is not failure evidence.
- A delegated response ending with `finish=length` is output truncation, not a successful empty handoff. When its mandatory artifact is absent the delegation is recorded `BLOCKED` with both the truncation and missing-artifact reasons; do not silently retry inside the same handoff.
- Run `teamflow source-check` after implementation edits and before test execution.

## Outer loop observation

An outer coordinator that watches this inner loop must load `observe-inner-loop` and observe metadata only. It is not doing the work, so it must not pay for the inner loop's context.

- Observe with one blocking call: `teamflow wait --run-id <id> --since <seq>`. It returns when events arrive or the timeout expires, so an unchanged inner loop costs nothing and there is no polling interval to tune. Omit `--run-id` to discover runs from the shared spool.
- Escalate only on a status that warrants it: `teamflow handoff status --run-id <id> [--id <handoff-id>]` for one receipt or every active handoff, and `teamflow agents list` for who is doing what.
- Confirm progress by testing existence and non-emptiness of expected paths under `.teamflow/runs/`; do not read artifact bodies.
- Never read session files, prompts, reasoning, model responses, raw provider errors, configuration, or credentials.
- There are exactly two stop signals: a handoff finished `BLOCKED`, and `runner_exited` arriving while the last business event is not terminal. A running handoff reporting `stale: true` past `TEAMFLOW_HANDOFF_TIMEOUT_SECONDS` is an age, not a failure.
- Terminal silence and elapsed wall time alone are never failure evidence and must not terminate the inner loop.

## Shared memory

Basic Memory data is local under `~/.teamflow/memory/`. Use `teamflow memory`; do not start cloud sync or a server process.

- Recall at task start and verify every remembered claim.
- Automated task capture must use `teamflow memory-capture`. Direct `remember` commands are reserved for explicit manual use, not planner fallback.
- Preserve source, reason, evidence, and constraints.
- Never store secrets, private data, raw conversations, full logs, guesses, or temporary failures.
- Do not write memory unless verification reports `PASS`.
