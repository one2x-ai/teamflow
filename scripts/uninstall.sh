#!/usr/bin/env bash
# Remove Teamflow install traces. Global launcher by default; project runtime
# with --project; shared memory data only with an explicit --memory flag.
set -euo pipefail

TEAMFLOW_HOME="${TEAMFLOW_HOME:-$HOME/.teamflow}"
MEMORY_ROOT="${TEAMFLOW_MEMORY_HOME:-$TEAMFLOW_HOME/memory}"
LAUNCHER_DIR="${TEAMFLOW_BIN_DIR:-$HOME/.local/bin}"
LAUNCHER_PATH="$LAUNCHER_DIR/teamflow"

DRY_RUN=false
REMOVE_MEMORY=false
PROJECT_INPUT=""

usage() {
  cat <<'EOF'
Usage: ./scripts/uninstall.sh [--project <path>] [--memory] [--dry-run]

Remove Teamflow install traces.

Default (no options) removes global traces only:
  - the launcher command at $TEAMFLOW_BIN_DIR/teamflow (default
    ~/.local/bin/teamflow), and only when it carries the teamflow marker

  --project <path>  Also clean that project: delete its .teamflow/ runtime
                    and remove the installer's .teamflow/ .gitignore entry.
  --memory          Also delete the shared memory data under
                    $TEAMFLOW_MEMORY_HOME (default ~/.teamflow/memory).
                    This is user knowledge and is never removed otherwise.
  --dry-run         Report what would be removed and change nothing.
  -h, --help        Show this help.

Never removes $HOME, an unrelated launcher, or a project's own files.
EOF
}

while (( $# > 0 )); do
  case "$1" in
    --project)
      shift
      [[ $# -gt 0 ]] || { echo "error: --project requires a path" >&2; exit 1; }
      PROJECT_INPUT="$1"
      ;;
    --memory) REMOVE_MEMORY=true ;;
    --dry-run) DRY_RUN=true ;;
    -h|--help) usage; exit 0 ;;
    *) echo "error: unknown argument: $1" >&2; usage >&2; exit 1 ;;
  esac
  shift
done

act() {
  if [[ "$DRY_RUN" == true ]]; then
    printf 'would remove %s\n' "$1"
  else
    printf 'removed %s\n' "$1"
  fi
}

# --- global launcher -------------------------------------------------------
if [[ -f "$LAUNCHER_PATH" ]]; then
  if grep -q 'agent-teamflow-launcher' "$LAUNCHER_PATH"; then
    act "$LAUNCHER_PATH"
    [[ "$DRY_RUN" == true ]] || rm -f "$LAUNCHER_PATH"
  else
    echo "skipped unrelated command: $LAUNCHER_PATH" >&2
  fi
else
  echo "no launcher at $LAUNCHER_PATH"
fi

# --- global memory browser -------------------------------------------------
SERVER_DIR="$TEAMFLOW_HOME/server"
if [[ -d "$SERVER_DIR" ]]; then
  act "$SERVER_DIR"
  [[ "$DRY_RUN" == true ]] || rm -rf "$SERVER_DIR"
else
  echo "no memory browser at $SERVER_DIR"
fi

# --- optional project runtime ---------------------------------------------
if [[ -n "$PROJECT_INPUT" ]]; then
  PROJECT_ROOT="$(git -C "$PROJECT_INPUT" rev-parse --show-toplevel 2>/dev/null || true)"
  if [[ -z "$PROJECT_ROOT" ]]; then
    echo "error: not a Git work tree: $PROJECT_INPUT" >&2
    exit 1
  fi

  RUNTIME_DIR="$PROJECT_ROOT/.teamflow"
  if [[ -d "$RUNTIME_DIR" ]]; then
    act "$RUNTIME_DIR"
    [[ "$DRY_RUN" == true ]] || rm -rf "$RUNTIME_DIR"
  else
    echo "no runtime at $RUNTIME_DIR"
  fi

  # Drop only the installer's own entry and its comment line, leaving every
  # other .gitignore rule untouched.
  GITIGNORE="$PROJECT_ROOT/.gitignore"
  if [[ -f "$GITIGNORE" ]] && grep -qxF '.teamflow/' "$GITIGNORE"; then
    act "$GITIGNORE entry .teamflow/"
    if [[ "$DRY_RUN" != true ]]; then
      python3 - "$GITIGNORE" <<'PY'
import sys
from pathlib import Path

path = Path(sys.argv[1])
removed = {"# Local Teamflow runtime", ".teamflow/"}
lines = path.read_text(encoding="utf-8").splitlines()
kept = [line for line in lines if line.strip() not in removed]
while kept and not kept[-1].strip():
    kept.pop()
path.write_text("".join(f"{line}\n" for line in kept), encoding="utf-8")
PY
    fi
  else
    echo "no .teamflow/ entry in $GITIGNORE"
  fi
fi

# --- optional shared memory ----------------------------------------------
if [[ "$REMOVE_MEMORY" == true ]]; then
  if [[ -d "$MEMORY_ROOT" ]]; then
    act "$MEMORY_ROOT"
    [[ "$DRY_RUN" == true ]] || rm -rf "$MEMORY_ROOT"
  else
    echo "no memory data at $MEMORY_ROOT"
  fi
else
  echo "kept shared memory data at $MEMORY_ROOT (pass --memory to delete)"
fi

if [[ "$DRY_RUN" == true ]]; then
  echo ""
  echo "Dry run only. Nothing was changed."
fi
