---
name: memory-recall
description: Recall prior verified work from the shared local Basic Memory store at task start. Use for cross-project memory search, note reads, and graph context before planning or editing.
---

# Memory Recall

Use the repository wrapper for consistent local-only settings.

## Resume workflow

1. Inspect recent activity:

   ```bash
   teamflow memory list
   ```

2. Search using distinctive task, component, or decision terms:

   ```bash
   teamflow memory recall "<task keywords>"
   ```

3. Read the best candidate:

   ```bash
   teamflow memory read "memory://<note-permalink>"
   ```

4. Traverse connected notes when relations matter:

   ```bash
   teamflow memory context "memory://<topic>/*"
   ```

5. Verify recalled claims against the current repository before planning or editing.

## Selection rules

- Prefer repository-specific notes when they match the current Git remote.
- Use global notes only for practices that genuinely transfer across repositories.
- Treat stale or conflicting notes as leads, not authority.
- If memory is unavailable, continue from current files and report the memory failure separately.
- Capture the new verified outcome after the task passes through `teamflow memory-capture`; pass every relevant recalled permalink in the task receipt so formatting can deduplicate or defer conflicts.

## Safety

- Keep memory local. Never use `--cloud`, cloud workspaces, login, or MCP commands.
- Never store secrets, credentials, private user data, raw conversations, full logs, or unverified claims.
- `remember` and `remember-global` are manual operations only; automated capture must use `teamflow memory-capture` with a verified-task receipt.
