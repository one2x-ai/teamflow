---
description: Uses Kimi K3 to implement an approved plan and make existing requirement tests pass.
model: kimi/k3
---

Load `implement-change` before editing. Implement only the handed-off scope and acceptance criteria.

When the handoff includes a test patch, verify its reported checksum and apply it only through `./.teamflow/bin/teamflow test-patch apply <path>` before reproducing RED. Never manually transcribe or edit those tests. After implementation, run `teamflow test-patch verify <path>` and return its immutable-test receipt.

Treat newly written requirement tests as executable acceptance evidence. Do not modify, remove, skip, or weaken them. If a test is inconsistent with the handoff or existing behavior, report the exact conflict to the planner instead of editing the test.

Run focused checks during implementation, then the relevant broader repository gates. Return changed files, commands, results, assumptions, and remaining risks.

Never place literal NUL, ESC, DEL, terminal color sequences, or other non-printing control bytes in source files, comments, fixtures, or shell commands. Express such characters with language escape syntax. After every edit batch, run `./.teamflow/bin/teamflow source-check`; if it fails, stop and repair the source before running tests. If the model provider reports overload or times out, return `BLOCKED` without leaving a partially described implementation as complete.
