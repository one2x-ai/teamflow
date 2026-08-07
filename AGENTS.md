# Teamflow Agent Instructions

This repository defines and evolves Teamflow, a multi-agent coding system.

## Architectural self-consistency

Any change to this repository — product code, tests, scripts, prompts, or documentation — goes through teamflow's agents. An observe loop may write the handoff, observe event metadata, verify artifacts, and commit; it may not implement, fix, or refactor. When a delegation returns something wrong, the observe loop collects the failure receipt and delegates again. This exercises the process on itself so defects surface here, not in business projects.

Narrow exceptions: writing or correcting the handoff itself; reverting a bad change with git; temporarily breaking something to prove a test fails, then restoring it (verification, not implementation); emergency recovery when the agent path itself is broken — say so in the report.

## Roles

- `planner` uses GLM-5.2 and owns requirement analysis, acceptance criteria, delegation, and the final summary.
- `test-writer` uses GLM-5.2 and owns requirement-focused test design and final assertion/diff review.
- `test-runner` owns test execution and structured failure receipts without file edits.
- `coder` uses Kimi K3, focuses on product-code implementation, and must not redefine the acceptance criteria.
- `command` uses MiMo 2.5 Pro for explicit shell, Git, and GitHub operations that need semantic interpretation but no code edits or multi-agent planning.
- `supervisor` uses MiMo 2.5 Pro for deterministic mechanical checks — artifact existence, checksums, and test-patch gates — without editing files or delegating.
- `title-compressor` uses MiMo 2.5 Pro to compress a delegation into one registry title line; failures degrade gracefully and never block the run.
- `memory-indexer` uses MiMo 2.5 Pro to generate TurnIndex XML for cold-stored TurnBlocks without modifying product code or Basic Memory.
- `emotional-salience-sensor`, `memory-compressor`, `memory-extractor`, and `memory-formatter` form the curated serial memory pipeline; the sensor uses MiMo 2.5 Pro and GLM-5.2 owns both extraction and final formatting. Models may write only below `.teamflow/runs/memory/` and must never write Basic Memory directly.

Only depth-0 roles with the strict boolean frontmatter declaration `delegates: true` may receive `task` and `task_group`. Child roles always run at depth 1 and may never delegate further.

Use this sequence unless the request is documentation-only or cannot be tested:

1. Inspect the repository and clarify the observable outcome.
2. Recall relevant cross-project memory and verify it against the current repository.
3. Write a plan with explicit acceptance criteria.
4. Ask `test-writer` to add focused tests and provide exact execution commands.
5. Ask `test-runner` to execute them and return a structured failure receipt.
6. Ask `coder` to implement the smallest coherent change.
7. Ask `test-runner` to execute focused and relevant regression checks.
8. Ask `test-writer` to review the assertions and final diff.
9. Persist only verified, reusable findings after execution and review pass.
10. Report changed files, execution receipts, memory written, and remaining risks.

If test-first execution is skipped, state the concrete reason in the final report.

## Handoff contract

Coordination happens in handoffs: one unit of work moved from a delegator to a receiver, whose state the receiver maintains until a terminal status. A structured handoff is required for every delegation; author the body with the `write-handoff` skill. Do not hand off vague requests such as "fix it" or "make tests pass".

State changes and state queries are programmatic; requirement expression and orchestration go through models and prompts. `teamflow handoff open/start/finish/status/list` owns every transition, sequence allocation, receipt validation, and event delivery; agents write bodies, receipts, and diagnoses. Nothing may hand-write `state.json`, an event file, an `active/` sentinel, or a liveness record.

## Engineering rules

- Read the repository's existing instructions before editing.
- Preserve user changes and avoid unrelated rewrites.
- Never expose, print, or commit API keys and secrets.
- Do not weaken assertions merely to make a test pass.
- Prefer focused tests first, then the repository's broader lint, typecheck, test, and build gates.
- Do not run `git push`, destructive reset, or workspace-cleaning commands unless the user explicitly requests them.
- Record teamflow run artifacts only below `.teamflow/runs/`.
- Every delegation is a registered handoff; explicit provider timeout, authentication failure, quota exhaustion, overload, transport failure, or user cancellation finishes it `BLOCKED` instead of silently restarting it. Silence and elapsed wall time alone are not failures. `blocked.reason` is one enum: `CONTEXT_BUDGET_EXCEEDED`, `RECALL_BUDGET_EXCEEDED`, `DELEGATION_ARTIFACT_MISSING`, `OUTPUT_TRUNCATED`, `PROVIDER_FAILURE`, `USER_CANCELLED`.
- A delegated response ending with `finish=length` is output truncation, not a successful empty handoff. When its mandatory artifact is absent the handoff is recorded `BLOCKED` with both the truncation and missing-artifact reasons; do not silently retry inside the same handoff.
- Run `teamflow source-check` after code edits to reject accidental non-printing control bytes.
- Keep target-project integration limited to the standard `.teamflow/` entry in `.gitignore`; do not scatter runtime files across the repository root.
- Keep Agent prompts and Skills concise; put shared policy here instead of duplicating it.
- Put a test next to the code it exercises: `tests/` for `scripts/` and repository-level contract tests, `tests/runtime/` for the installable runtime (grouped by subject: `extensions/`, `agents/`, `skills/`, `bin/`), `.teamflow/extensions/**/*.test.ts` for extension pure-logic modules (bun test), `server/tests/` for the Bun service. Neither `tests/runtime/`, extension `.test.ts` files, nor `server/tests/` may ship to a target project.
- Keep `.teamflow/` limited to Pi-agent runtime content. Another harness's config (`openai.yaml`, `CLAUDE.md`, `.codex/`) belongs in `.teamflow/.gitignore`, not in the managed file set.
- Install product files only. Teamflow's own development context — test suites, `runs/`, `sessions/`, credentials, `docs/`, and the repository-level `AGENTS.md` and `README.md` — must never reach a business project.

## Observe loop observation

External coordinators observe metadata only. Wait on one blocking call — `teamflow wait --run-id <id> --since <seq>` — and escalate to `teamflow handoff status --run-id <id>` or `teamflow agents list` only when a returned status warrants it; check expected artifact existence below `.teamflow/runs/`. Never read session files, prompts, reasoning, responses, raw errors, configuration, or credentials. There are exactly two stop signals: a handoff finished `BLOCKED`, and `runner_exited` arriving while the last business event is not terminal. Terminal silence alone is not a failure and must not terminate the execute loop.

## Cross-project memory

Basic Memory is the fully local shared memory backend. This repository contains only Teamflow definitions and initialization logic; it is not a working-project or memory-data repository. Cross-project runtime memory lives under `~/.teamflow/memory/`: Markdown source files in `knowledge/`, and Basic Memory config, SQLite index, logs, and optional FastEmbed cache in `state/`. Do not start or configure MCP, cloud sync, accounts, or API keys. The planner owns all memory access through `teamflow memory`; implementation and test agents receive relevant context through their handoffs.

- Recall at the start of a task, but validate every recalled claim against current files and commands.
- Store only durable decisions, reproducible fixes, repository conventions, and verified commands.
- Include the reason and evidence in the memory text; avoid context-free conclusions.
- Use `remember` for repository-specific findings and `remember-global` only for practices proven reusable across projects.
- Never store secrets, credentials, private user data, raw conversations, full logs, unverified hypotheses, or temporary failures.
- Do not write memory when verification fails or remains blocked.
- Correct stale memory by writing the new verified fact with explicit supersession context; do not silently rely on the old entry.

## Maintaining this repository

When changing Agent models, permissions, the handoff lifecycle or event protocol, environment variables, frontmatter contracts, or scripts:

1. Update the implementation.
2. Update README usage and architecture notes.
3. Update `.teamflow/AGENTS.md` shared constraints when frontmatter contracts change.
4. Run `./scripts/doctor.sh`.
5. Confirm all project Agents and Skills appear in `teamflow debug` output.
6. Dry-run `./scripts/install.sh` against a disposable Git project when installable files change.
7. Keep the four Basic Memory Skills CLI-only; use `./scripts/update.sh` to prepare upstream refreshes.
