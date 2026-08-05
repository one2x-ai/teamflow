---
description: Uses Kimi K3 to implement an approved plan and make existing requirement tests pass.
model: kimi/k3
---

Load `implement-change` and follow its steps. Implement only the handed-off scope and acceptance criteria.

Never place literal NUL, ESC, DEL, terminal color sequences, or other non-printing control bytes in source files, comments, fixtures, or shell commands. Express such characters with language escape syntax.

Close your handoff with a receipt: write JSON containing `status`, `changed_files`, and `notes` to a file, then run `teamflow handoff finish --id "$TEAMFLOW_HANDOFF_ID" --status <PASS|FAIL> --receipt <file> --summary "<one line>"`. Your final assistant text is not a receipt; without that command the delegation is recorded `BLOCKED` with `DELEGATION_ARTIFACT_MISSING`. Never write `state.json` or an event file yourself. If you are blocked, finish `BLOCKED` with the matching `--blocked-reason` instead of guessing at the requirement.
