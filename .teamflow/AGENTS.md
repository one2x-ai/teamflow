# Teamflow Agent Instructions

This working repository uses Teamflow's test-first multi-agent process.

## Roles

- `planner` uses GLM-5.2 and owns requirement analysis, acceptance criteria, delegation, memory recall, and the final report.
- `test-writer` uses GLM-5.2 and owns requirement-focused test design before implementation and assertion/diff review afterward.
- `test-runner` owns test execution and structured error receipts; it never edits files.
- `coder` uses Kimi K3, focuses on the smallest coherent product-code implementation, and must not redefine acceptance criteria.
- `command` uses MiMo 2.5 Pro for explicit shell, Git, and GitHub operations that require no code edits or multi-agent planning.
- `emotional-salience-sensor`, `memory-compressor`, `memory-extractor`, and `memory-formatter` form the serial capture pipeline; GLM-5.2 owns both extraction and final formatting. Models write only below `.teamflow/runs/memory/`; deterministic apply writes safe new notes and defers update/supersede proposals.

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

Every handoff must include goal, scope, acceptance criteria, constraints, evidence already obtained, and open questions. Do not hand off vague requests.

## Engineering rules

- Preserve existing project instructions and user changes.
- Never expose, print, or commit secrets.
- Never weaken assertions merely to make a test pass.
- Run focused checks before broader lint, typecheck, test, and build gates.
- Do not push, force-reset, or clean the workspace without explicit authorization.
- Put temporary teamflow run artifacts below `.teamflow/runs/`.
- Wrap delegated code phases with `teamflow phase start/finish`; explicit provider timeout, authentication, quota, overload, transport failure, and user cancellation are `BLOCKED`, not implicit retries of the full Teamflow process. There is no local wall-time timeout by default, so silence while a provider queues is not failure evidence.
- A delegated response ending with `finish=length` is output truncation, not a successful empty handoff. If its mandatory artifact is absent, finish the phase as `BLOCKED` with the truncation and missing-artifact reasons; do not silently retry inside the same phase.
- Run `teamflow source-check` after implementation edits and before test execution.

## Shared memory

Basic Memory data is local under `~/.teamflow/memory/`. Use `teamflow memory`; do not start cloud sync or a server process.

- Recall at task start and verify every remembered claim.
- Automated task capture must use `teamflow memory-capture`. Direct `remember` commands are reserved for explicit manual use, not planner fallback.
- Preserve source, reason, evidence, and constraints.
- Never store secrets, private data, raw conversations, full logs, guesses, or temporary failures.
- Do not write memory unless verification reports `PASS`.
