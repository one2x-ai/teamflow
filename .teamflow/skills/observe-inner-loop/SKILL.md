---
name: observe-inner-loop
description: Detect a running Teamflow inner loop's execution path from metadata only. Use when an outer coordinator must know which phase is running, whether it is BLOCKED, and whether expected artifacts exist, without paying for the inner loop's context.
---

# Observe the Inner Loop

You are the outer loop. You do not do the work, so observe cheaply: probe first, escalate only when something changed.

## Rung 1 — liveness probe (default, every poll)

```bash
python3 .teamflow/skills/observe-inner-loop/scripts/probe.py --run-id <id> [--pid <pid>]
```

One line out — `state=alive|exited|unknown activity=<Ns> fp=<phase:status:size>` — nothing else. No turn counts, tool names, event dumps, or file bodies. While `state=alive` and `fp` is unchanged from the last poll, report nothing and spend no tokens: this is the steady state of a working run and needs no log parsing. `activity` (seconds since the current phase file was written) grows during a long phase and is not failure evidence.

## Rung 2 — read the phase receipt (only when fp changed or state=exited)

```bash
teamflow phase status --run-id <id>              # current phase receipt
teamflow phase status --run-id <id> --phase <n>  # a historical phase receipt
```

Costs one small receipt, read only on a transition. `RUNNING` with `stale: true` means observation time exceeded `TEAMFLOW_PHASE_TIMEOUT_SECONDS`; it is not a failure and must not terminate the run. `BLOCKED` is the only real stop signal — read its `block_reason`. `PASS` or `FAIL` ends that phase.

## Rung 3 — artifact existence (only at a phase boundary)

Confirm expected paths under `.teamflow/runs/` exist and are non-empty (`runs/phases/`, `runs/test-patches/<id>/tests.patch`, `runs/task-receipts/<id>/receipt.json`). Test existence and size; do not read bodies. `teamflow session list --format json` is the costliest rung — use it sparingly, never as the default.

## Invariants

- Poll at least 30 seconds apart. Never in a tight loop.
- Report only transitions: phase entered, phase finished, BLOCKED raised. While status and phase are unchanged, report nothing.
- Terminal silence and elapsed wall time alone are never failure evidence; a provider may be queueing.

## Isolation

Read-only: never write, edit, or delete. Never read session files, prompts, reasoning, model responses, raw provider errors, configuration, or credentials. Metadata receipts and artifact existence are the entire observation surface.
