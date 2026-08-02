---
description: Uses MiMo 2.5 Pro to generate TurnIndex entries for cold-stored TurnBlocks without modifying product code or Basic Memory.
model: mimo/mimo-v2.5-pro
---

Read the supplied TurnBlock XML and generate a canonical TurnIndex XML following the turn-index.ts schema. Each semantic conclusion (actions, outcomes, decisions, constraints, failures, open_questions) must reference the original TurnBlock message/tool event via IndexSourceRef (messageId, field, optional toolCallId). Apply secret redaction to all extracted text.

Write the index output ONLY to the path supplied in the handoff, which will be below `.teamflow/runs/memory/` or the cold-store index directory. Never modify product code, source code, or test files. Never write to Basic Memory knowledge or the knowledge/ directory. The index is a derived artifact that can be deleted and rebuilt; it never replaces the original TurnBlock.
