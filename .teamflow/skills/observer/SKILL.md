---
name: observer
description: Observe a running Teamflow execute loop from metadata only. Use when an outer coordinator must know what the execute loop is doing, whether it is blocked, and whether expected artifacts exist, without paying for the execute loop's context.
---

# Observe the Execute Loop

You are the observe loop, not the work: one blocking call, never a poll — an unchanged execute loop must cost you nothing.

## The whole surface

```bash
teamflow wait --run-id <id> --since <seq> [--kind <k>,...] [--timeout 600]
```

It suspends until events arrive or the timeout expires, then returns `{"run_id", "seq", "events"}`. Every field comes from the event's file name — `<seq>--<subject>--<kind>--<status>.json` — so a body read is the exception. Pass the returned `seq` back as `--since` next time; reconnecting never replays.

Without `--run-id` it watches the shared spool and reports run-level events, use it to discover runs you did not start. `teamflow run` prints `run_id=` on stderr when it starts one.

Kinds: `run_started`, `run_finished`, `handoff_opened`, `handoff_finished`, `artifact_written`, `runner_exited`.

## Escalating

Only when a `handoff_finished` status warrants a closer look:

- `teamflow handoff status --run-id <id> [--id <handoff-id>]` — one receipt, or every active handoff.
- `teamflow agents list` — role, depth, heartbeat age, handoff, title, scope.
- the enum fields of the event's `ref` or of `receipt.json` — never the prose around them.

Confirm an expected artifact under `.teamflow/runs/` by existence and non-emptiness. Do not read its body.

## Stop signals

Exactly two:

1. a handoff finished `BLOCKED` — read its `reason`;
2. `runner_exited` arrived while the last business event was not terminal — the execute loop died without finishing.

Nothing else stops the loop. Terminal silence and elapsed wall time are never failure evidence and must never terminate the execute loop. A long handoff reports `stale: true` past `TEAMFLOW_HANDOFF_TIMEOUT_SECONDS`; that is an age, not a verdict.

## Forbidden liveness checks

Use `teamflow agent-status` for "is this agent alive" calls. Never conclude death from one signal:

- ❌ `ps aux | grep` — may be absent; empty output is tool failure, not death.
- ❌ Log size / growth — may be filtered (`streaming-filter`) or buffered.
- ❌ `stale: true` on a running handoff — an age, not a verdict.
- ❌ Absence of `runner_exited` — watchdog fires on detection.

Fallback: require ≥2 of `/proc/<pid>` exists, `os.kill(pid, 0)`, cmdline contains `"pi"`, fresh heartbeat in `liveness/`.

## Isolation

Observation is read-only: never write, edit, or delete. Never read session files, prompts, reasoning, model responses, raw provider errors, configuration, or credentials. `events/`, `state.json`, and `receipt.json` are the metadata plane built for you; everything else belongs to the execute loop.
