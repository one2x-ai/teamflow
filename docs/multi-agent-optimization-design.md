# Teamflow Multi-Agent Optimization Design

## Background

Teamflow's current multi-agent workflow is strictly sequential: planner → test-writer → test-runner → coder → test-runner → test-writer → planner. Each delegation spawns an isolated Pi child process with full context injection. This design document identifies optimization opportunities in four dimensions: parallelization, delegation overhead, context compression, and supervision patterns.

## Goals

- Reduce wall-clock time for multi-agent tasks through bounded parallelization
- Reduce per-delegation token and process overhead
- Reduce handoff message size through structured evidence compression
- Reduce planner context burden through supervisor delegation

## Non-Goals

- Changing the fundamental test-first ordering (RED before implementation)
- Replacing the Pi runtime or extension architecture
- Adding new LLM providers or models
- Automatic retry loops (explicit failure handling remains)

## Current Architecture Analysis

### Sequential Bottlenecks

```
planner (4KB prompt + 6.2KB skills + 10.8KB AGENTS.md)
  → test-writer (2.6KB + 2.7KB skill + 10.8KB AGENTS.md) [serial per file pair]
    → test-runner (1.4KB, no AGENTS.md) [new process per command]
      → coder (0.4KB + 1.2KB skill + 10.8KB AGENTS.md)
        → test-runner again [new process]
          → test-writer again
            → planner writes receipt + memory-capture [4 serial model calls]
```

**Identified bottlenecks:**
1. test-writer processes file pairs serially even when modules are independent
2. test-runner spawns a new Pi process per command (cold start overhead)
3. Handoff evidence includes raw test output (verbose, high token)
4. Planner performs mechanical checks (artifact existence, checksum) inline
5. Memory capture runs 4 model stages serially with no parallelism

## Design

### 1. Bounded Parallel Test Generation

**Problem**: test-writer processes one module/file pair at a time. For a task touching 3 independent modules, this means 3 sequential delegations with full context reload each time.

**Solution**: Use `task_group` to parallelize independent file-pair test generation.

**Mechanism**:
- planner identifies independent module pairs during planning
- If pairs have no shared state or ordering dependency, spawn a `task_group` with one `test-writer` task per pair
- Each test-writer writes to the same `tests.patch` but with a per-pair lock (planner assigns disjoint file scopes)
- planner merges and validates the combined patch before proceeding

**Constraints**:
- Max 3 concurrent test-writer children (matches `MAX_CONCURRENCY` pattern)
- Each child must declare its assigned file scope in the handoff to prevent patch conflicts
- If any pair fails, the entire group is marked FAIL and planner serializes the remainder

**Files to modify**:
- `.teamflow/agents/planner.md`: add parallel test-generation decision logic
- `.teamflow/skills/write-tests/SKILL.md`: document per-pair scope isolation
- `.teamflow/extensions/teamflow-task/index.ts`: no change needed (task_group already exists)

**Estimated savings**: 40-60% wall time for multi-module tasks.

### 2. Test-Runner Process Pooling

**Problem**: Each test-runner delegation spawns a new Pi child process (~200-500ms cold start). For a typical task with 3 test-runner invocations (RED, focused PASS, regression), this is ~1.5s of pure process overhead.

**Solution**: Add a lightweight `test-runner-batch` mode that accepts multiple commands in one delegation.

**Mechanism**:
- Instead of 3 separate `task(test-runner, "run command A")`, `task(test-runner, "run command B")`, etc.
- Planner sends one `task(test-runner, JSON)` where the prompt contains a JSON array of commands:
  ```json
  {
    "commands": [
      {"id": "red", "cmd": "pytest tests/test_foo.py -x", "expect": "FAIL"},
      {"id": "focused", "cmd": "pytest tests/test_foo.py -v", "expect": "PASS"},
      {"id": "regression", "cmd": "pytest tests/ -v", "expect": "PASS"}
    ]
  }
  ```
- test-runner executes commands sequentially in one process, returns a JSON array of receipts
- Planner parses the batch receipt and maps each result to its phase

**Alternative considered**: Long-running test-runner daemon. Rejected because it breaks Pi's stateless extension model and complicates cancellation.

**Files to modify**:
- `.teamflow/agents/test-runner.md`: accept batch command format
- `.teamflow/agents/planner.md`: batch test-runner delegations when possible

**Estimated savings**: ~1s wall time per task, ~2KB token per saved process spawn.

### 3. Structured Evidence Compression

**Problem**: Handoff evidence includes raw test output (stack traces, assertion diffs). A single failing test can produce 2-5KB of output, most of which is irrelevant to the next agent.

**Solution**: Planner compresses evidence before including it in handoffs.

**Mechanism**:
- After receiving a test-runner receipt, planner extracts only:
  - `failed_checks`: names of failing tests
  - `error_excerpt`: the shortest stderr/stdout excerpt (already in receipt schema)
  - `diagnosis`: evidence-based likely cause
- Raw command output is stored in `.teamflow/runs/evidence/<run-id>/` but not passed in handoffs
- Handoff evidence section uses a compact format:
  ```
  Evidence:
  - red: pytest tests/test_foo.py::test_bar FAILED (assert 1 == 2)
  - focused: 3 passed in 0.12s
  ```

**Files to modify**:
- `.teamflow/agents/planner.md`: evidence compression rules
- `.teamflow/skills/plan-change/SKILL.md`: document compact evidence format
- `.teamflow/bin/teamflow`: optionally add `teamflow evidence <run-id>` to inspect raw logs

**Estimated savings**: 1-3KB per handoff, 2-6KB per task.

### 4. Supervisor Role for Mechanical Checks

**Problem**: Planner performs mechanical checks inline (verify `tests.patch` exists, run `test-patch check`, validate checksums). These consume planner tokens and attention but require no reasoning.

**Solution**: Introduce a lightweight `supervisor` role (MiMo 2.5 Pro) that handles deterministic validation.

**Mechanism**:
- New agent: `.teamflow/agents/supervisor.md`
- Frontmatter: `needs_project_rules: false`, `tools: read,bash`
- Responsibilities:
  - Verify artifact existence and checksums
  - Run `teamflow test-patch check/verify`
  - Return structured pass/fail receipts
- Planner delegates mechanical checks to supervisor instead of running them inline

**Planner workflow change**:
```
Before: planner → test-writer → [planner checks patch] → coder
After:  planner → test-writer → supervisor → coder
```

**Benefits**:
- Planner prompt shrinks (remove mechanical check instructions)
- Planner context stays focused on coordination, not validation
- Supervisor can run checks in parallel with other planning work

**Files to create/modify**:
- `.teamflow/agents/supervisor.md` (new)
- `.teamflow/agents/planner.md`: delegate mechanical checks
- `.teamflow/AGENTS.md`: document supervisor role
- `README.md`: add supervisor to agent table

**Estimated savings**: ~500 bytes from planner prompt, better separation of concerns.

### 5. Memory-Capture Parallel Emotion + Compression

**Problem**: Memory capture runs 4 serial stages: emotion-sensor → compressor → extractor → formatter. Emotion-sensor output is optional metadata for compressor; they can run in parallel.

**Mechanism**:
- Current: emotion → compressor → extractor → formatter
- Optimized: (emotion ∥ compressor) → extractor → formatter
- Compressor receives emotion signals as optional input if available, but doesn't block on it

**Implementation**:
- Modify `.teamflow/skills/memory-capture/scripts/capture_memory.py` to spawn emotion and compression concurrently
- Use `task_group` with 2 tasks, then join before extraction
- If emotion fails or times out, compressor proceeds without it

**Files to modify**:
- `.teamflow/skills/memory-capture/scripts/capture_memory.py`
- `.teamflow/agents/memory-compressor.md`: document optional emotion input

**Estimated savings**: ~30% of memory-capture wall time (emotion and compression are the two slowest stages).

## Implementation Plan

### Phase 1: Quick Wins (Low Risk)
1. Structured evidence compression (planner prompt only)
2. Test-runner batch mode (test-runner prompt + planner delegation pattern)
3. Supervisor role creation

### Phase 2: Parallelization (Medium Risk)
4. Bounded parallel test generation
5. Memory-capture emotion/compression parallelization

## Success Metrics

- Wall time reduction for a standard 3-module task: target 30-50%
- Token reduction per task: target 3-8KB
- No increase in BLOCKED rate from parallelization conflicts
- All existing tests continue to pass

## Risks and Mitigations

| Risk | Impact | Mitigation |
|------|--------|------------|
| Parallel test-writer patch conflicts | High | Disjoint file scopes enforced by planner; fallback to serial on any conflict |
| Batch test-runner loses per-command isolation | Medium | Commands run sequentially in one process; failure in one doesn't affect others |
| Evidence compression loses debugging info | Medium | Raw logs persisted to `.teamflow/runs/evidence/` for post-hoc inspection |
| Supervisor adds another role to maintain | Low | Supervisor is optional; planner can still run checks inline if supervisor unavailable |

## Open Questions

1. Should supervisor be a fixed role or a mode of test-runner? (Leaning: fixed role for clarity)
2. What is the optimal max concurrency for parallel test-writer? (Leaning: 3, matching existing MAX_CONCURRENCY)
3. Should batch test-runner support parallel command execution? (Leaning: no, sequential is simpler and sufficient)
