---
name: observe-inner-loop
description: Detect a running Teamflow inner loop's execution path from metadata only. Use when an outer coordinator must know which phase is running, whether it is BLOCKED, and whether expected artifacts exist, without paying for the inner loop's context.
---

# Observe the Inner Loop

You are the outer loop. You do not do the work, so observe cheaply: probe first, escalate only when something changed.

## Two-command surface

| Command | Worst-case cost | Bound |
|---|---|---|
| `teamflow probe` | One `ps` call + stat of run dirs | One line, always |
| `teamflow session list --format json` | Reads at most 10 files | At most 10 entries, 6 keys each |

Reading a run's contents from the outer loop is unsupported. A question that needs them is a question the outer loop should not ask.

## Rung 1 — liveness probe (default, every poll)

```bash
teamflow probe
```

One line: `state=alive|exited|unknown activity=<Ns> fp=<phase:status:size>`.
With no arguments, discovers the newest run under `.teamflow/runs/code/` by directory mtime. Liveness checks the process table for a `pi` process; with concurrent delegations it reports whether any pi is running and cannot attribute a process to a specific run. Exit codes: `0` alive, `1` exited, `2` unknown — usable without parsing. `--run-id <id>` pins a run; `--pid <pid>` overrides process detection.

While `state=alive` and `fp` is unchanged, report nothing and spend no tokens. `activity` grows during a long phase and is not failure evidence.

## Rung 2 — phase receipt (only when fp changed or state=exited)

```bash
teamflow phase status --run-id <id>
```

One small receipt, read only on a transition. `RUNNING` with `stale: true` is not failure. `BLOCKED` is the only stop signal — read its `block_reason`. At a phase boundary, confirm expected artifacts under `.teamflow/runs/` exist and are non-empty; test existence and size, never bodies.

## Rung 3 — session summary (sparingly)

```bash
teamflow session list --format json
```

Bounded to the 10 newest files for the current working directory. Each entry: `id`, `model`, `provider`, `created`, `updated`, `message_count`. No message bodies, prompts, or responses. `--limit N` (N at most 10) lowers the count; above 10 is rejected.

## Isolation

Observation is read-only: never write, edit, or delete. Never read session files, prompts, reasoning, model responses, raw errors, or credentials — do not read artifact bodies; test existence and size only.

## Invariants

- Poll at least 30 seconds apart.
- Report only transitions. While unchanged, stay silent.
- Terminal silence and elapsed time are never failure evidence.
