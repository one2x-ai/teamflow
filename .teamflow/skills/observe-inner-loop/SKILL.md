---
name: observe-inner-loop
description: Detect a running Teamflow inner loop's execution path from metadata only. Use when an outer coordinator must know which phase is running, whether it is BLOCKED, and whether expected artifacts exist, without paying for the inner loop's context.
---

# Observe the Inner Loop

You are the outer loop. You do not do the work, so observe cheaply.

## Two commands

```bash
teamflow phase status --run-id <id>              # current phase receipt
teamflow phase status --run-id <id> --phase <n>  # a historical phase receipt
teamflow session list --format json              # id/model/provider/times/message_count
```

## Artifact existence

Check only that expected paths under `.teamflow/runs/` exist and are non-empty:
`runs/phases/`, `runs/test-patches/<run-id>/tests.patch`, `runs/task-receipts/<run-id>/receipt.json`, `runs/memory/<run-id>/`. Test existence and size; do not read bodies.

## Reading the receipt

- `status: RUNNING` plus `stale: true` means only that observation time exceeded `TEAMFLOW_PHASE_TIMEOUT_SECONDS`. It is not a failure and must not terminate the run.
- `status: BLOCKED` is the only real stop signal. Read its `block_reason`, and for budget failures its `required_action`.
- `status: PASS` or `FAIL` ends that phase; move to the next phase name.

## Polling discipline

- Poll at most every 30 seconds. Never poll in a tight loop.
- Compare the new receipt to the previous one; when `status` and `phase` are unchanged, report nothing and spend no tokens.
- Report only transitions: phase entered, phase finished, BLOCKED raised.
- Terminal silence and elapsed wall time alone are never failure evidence; a provider may be queueing.

## Isolation

Read-only: never write, edit, or delete anything. Never read session files, prompts, reasoning, model responses, raw provider errors, configuration, or credentials. Metadata receipts and artifact existence are the entire observation surface.
