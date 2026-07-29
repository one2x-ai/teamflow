---
name: plan-change
description: Convert a software change request into an executable, test-first plan with observable acceptance criteria and a precise agent handoff. Use when planning a feature, bug fix, refactor, migration, or other repository change before implementation begins.
---

# Plan a Change

1. Read repository instructions and inspect the affected code and tests.
2. Restate the requested outcome in observable terms. Separate confirmed facts from assumptions.
3. Define scope, non-goals, compatibility constraints, and failure risks.
4. Write acceptance criteria that a test or deterministic check can prove.
5. Identify the smallest useful test slice and the likely implementation area without prescribing unnecessary code details.
6. Produce the handoff below. Do not edit product or test code.

## Handoff

Use a compact structured format. Each field on its own line, no prose padding:

```text
Goal: <one observable outcome>
Scope: <files or components>
Acceptance:
- <verifiable criterion>
Constraints:
- <compatibility, security, non-goal>
Initial test target: <file or module>
Evidence collected:
- <command and result>
Open questions:
- <unresolved fact>
```

Rules:
- Reject vague criteria such as "works correctly", "is robust", or "tests pass". Name the input, behavior, and expected observable output.
- Do not repeat constraints already covered by the agent's system prompt (e.g., "never weaken assertions", "do not edit tests").
- Omit empty sections rather than writing "none" or "N/A".
- Keep evidence to commands already executed; do not narrate the exploration process.
