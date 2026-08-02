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

Reading a run's contents from the outer loop is unsupported.

## Rung 1 — liveness probe (default, every poll)

```bash
teamflow probe
```

One line: `state=alive|exited|unknown activity=<Ns> fp=<phase:status:size>`.
With no arguments, discovers the newest run under `.teamflow/runs/code/` by directory mtime. Exit codes: `0` alive, `1` exited, `2` unknown. `--run-id <id>` pins a run; `--pid <pid>` overrides process detection.

Liveness means the delegation's process is running, not that the current phase has finished — a PASS/FAIL/BLOCKED receipt still reports `alive` while the process lives. Without `--pid`, `_pi_running()` checks `ps -eo comm=` for any `pi` process; with concurrent delegations it cannot attribute one to a specific run, so a terminal phase may report `alive` while another delegation's pi runs. An unreadable process table yields `unknown` (exit 2) — never guessed from a receipt alone. With a dead process, `fp=...:RUNNING:...` means abandoned/crashed and `fp=...:PASS:...` a clean finish; both report `state=exited` — compare state to fp's status field.

While `state=alive` and `fp` is unchanged, report nothing and spend no tokens. `activity` grows during a long phase and is not failure evidence.

## Rung 2 — phase receipt (only when fp changed or state=exited)

```bash
teamflow phase status --run-id <id>
```

One small receipt, read only on a transition. `RUNNING` with `stale: true` is not failure. `BLOCKED` is the only stop signal — read its `block_reason`. At a phase boundary, confirm expected artifacts under `.teamflow/runs/` exist and are non-empty — never read bodies.

## Rung 3 — session summary (sparingly)

```bash
teamflow session list --format json
```

Bounded to the 10 newest files for the current working directory. Each entry: `id`, `model`, `provider`, `created`, `updated`, `message_count`. No message bodies, prompts, or responses. `--limit N` (N at most 10) lowers the count; above 10 is rejected.

## Isolation

Observation is read-only: never write, edit, or delete. Never read session files, prompts, reasoning, model responses, raw errors, or credentials.

## Invariants

- Poll at least 30 seconds apart.
- Report only transitions. While unchanged, stay silent.
- Terminal silence and elapsed time are never failure evidence.
