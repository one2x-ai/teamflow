#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

TEAMFLOW_HOME="${TEAMFLOW_HOME:-$HOME/.teamflow}"
MEMORY_ROOT="${TEAMFLOW_MEMORY_HOME:-$TEAMFLOW_HOME/memory}"
PROJECT_NAME="${TEAMFLOW_MEMORY_PROJECT:-${BASIC_MEMORY_PROJECT:-teamflow}}"
PROJECT_DIR="$MEMORY_ROOT/knowledge"

export BASIC_MEMORY_AUTO_UPDATE=false
export BASIC_MEMORY_CONFIG_DIR="$MEMORY_ROOT/state"
export BASIC_MEMORY_HOME="$PROJECT_DIR"
export BASIC_MEMORY_SEMANTIC_SEARCH_ENABLED=false

usage() {
  cat <<'EOF'
Usage: ./scripts/setup-memory.sh [--project NAME]

Initialize shared cross-project memory under ~/.teamflow/memory:
  knowledge/  Markdown source files
  state/      Basic Memory config, SQLite index, logs, and model cache

This Git repository contains only the teamflow definition and initializer.
No account, API key, cloud service, or MCP server is used.
EOF
}

while (( $# > 0 )); do
  case "$1" in
    --project)
      shift
      if (( $# == 0 )); then
        echo "error: --project requires a value" >&2
        exit 1
      fi
      PROJECT_NAME="$1"
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "error: unknown argument: $1" >&2
      usage >&2
      exit 1
      ;;
  esac
  shift
done

if ! command -v basic-memory >/dev/null 2>&1; then
  echo "error: basic-memory is not installed; run ./scripts/bootstrap.sh" >&2
  exit 1
fi

if ! command -v node >/dev/null 2>&1; then
  echo "error: node is required; run ./scripts/bootstrap.sh" >&2
  exit 1
fi

mkdir -p "$PROJECT_DIR" "$BASIC_MEMORY_CONFIG_DIR"
PROJECT_DIR="$(cd "$PROJECT_DIR" && pwd -P)"

if PROJECT_INFO="$(basic-memory project info "$PROJECT_NAME" --local --json 2>/dev/null)"; then
  CONFIGURED_PATH="$(node -e '
    const data = JSON.parse(process.argv[1]);
    process.stdout.write(data.project_path || "");
  ' "$PROJECT_INFO")"
  if [[ "$CONFIGURED_PATH" != "$PROJECT_DIR" ]]; then
    echo "error: Basic Memory project '$PROJECT_NAME' already points to '$CONFIGURED_PATH'" >&2
    echo "Expected shared knowledge directory: '$PROJECT_DIR'" >&2
    exit 1
  fi
  echo "Reusing Basic Memory project '$PROJECT_NAME'."
else
  basic-memory project add "$PROJECT_NAME" "$PROJECT_DIR" --default --local
fi

basic-memory project default "$PROJECT_NAME" --local >/dev/null
basic-memory reindex --search --project "$PROJECT_NAME" >/dev/null
basic-memory status --project "$PROJECT_NAME" --local
echo "Basic Memory cross-project store is ready."
echo "Markdown: $PROJECT_DIR"
echo "Database: $BASIC_MEMORY_CONFIG_DIR/memory.db"
