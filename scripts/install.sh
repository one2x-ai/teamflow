#!/usr/bin/env bash
set -euo pipefail

SOURCE_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TARGET_INPUT=""
DRY_RUN=false
FORCE=false
TEAMFLOW_HOME="${TEAMFLOW_HOME:-$HOME/.teamflow}"

usage() {
  cat <<'EOF'
Usage: ./scripts/install.sh [--dry-run] [--force] <target-project>

Install or update the current Teamflow runtime in an existing Git project.
Managed files stay below .teamflow/; the only root change is .gitignore.
EOF
}

while (( $# > 0 )); do
  case "$1" in
    --dry-run) DRY_RUN=true ;;
    --force) FORCE=true ;;
    -h|--help) usage; exit 0 ;;
    -*) echo "error: unknown option: $1" >&2; exit 1 ;;
    *)
      [[ -z "$TARGET_INPUT" ]] || { echo "error: provide exactly one target project" >&2; exit 1; }
      TARGET_INPUT="$1"
      ;;
  esac
  shift
done

[[ -n "$TARGET_INPUT" ]] || { usage >&2; exit 1; }
[[ -d "$TARGET_INPUT" ]] || { echo "error: target directory does not exist: $TARGET_INPUT" >&2; exit 1; }
TARGET_ROOT="$(git -C "$TARGET_INPUT" rev-parse --show-toplevel 2>/dev/null || true)"
[[ -n "$TARGET_ROOT" ]] || { echo "error: target must be inside an existing Git repository" >&2; exit 1; }
TARGET_ROOT="$(cd "$TARGET_ROOT" && pwd -P)"
[[ "$TARGET_ROOT" != "$SOURCE_ROOT" ]] || { echo "error: the Teamflow repository is not a target project" >&2; exit 1; }

for command_name in node pi basic-memory; do
  command -v "$command_name" >/dev/null 2>&1 || {
    echo "error: $command_name is required; run $SOURCE_ROOT/scripts/bootstrap.sh first" >&2
    exit 1
  }
done

FILES=(
  ".teamflow/models.json"
  ".teamflow/settings.json"
  ".teamflow/AGENTS.md"
  ".teamflow/bin/teamflow"
  ".teamflow/bin/pi-runtime"
  ".teamflow/bin/memory"
  ".teamflow/bin/memory-capture"
  ".teamflow/bin/test-patch"
  ".teamflow/bin/server"
  ".teamflow/extensions/teamflow-task/index.ts"
  ".teamflow/extensions/memory-context/index.ts"
  ".teamflow/extensions/memory-context/turn-block.ts"
  ".teamflow/extensions/memory-context/cold-memory-store.ts"
  ".teamflow/extensions/memory-context/file-cold-store.ts"
  ".teamflow/extensions/memory-context/rule-cache.ts"
  ".teamflow/extensions/memory-context/rule-cache-reducer.ts"
  ".teamflow/extensions/memory-context/turn-index.ts"
  ".teamflow/experiments/bin/memory-experiment"
  ".teamflow/experiments/bin/memory-compare"
  ".teamflow/experiments/scripts/compare_stage.py"
)
# Agent files (including memory-indexer.md) ship via the find loop below.
while IFS= read -r relative_path; do FILES+=("$relative_path"); done < <(
  cd "$SOURCE_ROOT"
  find .teamflow/agents .teamflow/skills -type f \
    ! -path '*/__pycache__/*' ! -name '*.pyc' -print | sed 's#^./##' | sort
)
while IFS= read -r relative_path; do FILES+=(".teamflow/$relative_path"); done < <(
  cd "$SOURCE_ROOT"
  find server -type f ! -path '*/node_modules/*' ! -name 'bun.lock' ! -name 'bun.lockb' \
    ! -path '*/__pycache__/*' ! -name '*.pyc' -print | sort
)

MANIFEST_PATH="$TARGET_ROOT/.teamflow/manifest.json"
sha256_file() { shasum -a 256 "$1" | awk '{print $1}'; }
manifest_hash() {
  [[ -f "$MANIFEST_PATH" ]] || return 0
  node -e 'const m=JSON.parse(require("fs").readFileSync(process.argv[1],"utf8"));process.stdout.write(m.files?.[process.argv[2]]||"")' \
    "$MANIFEST_PATH" "$1" 2>/dev/null || true
}

CONFLICTS=()
for relative_path in "${FILES[@]}"; do
  source_relative="${relative_path#.teamflow/}"
  if [[ "$relative_path" == .teamflow/server/* ]]; then
    source_path="$SOURCE_ROOT/${relative_path#.teamflow/}"
  else
    source_path="$SOURCE_ROOT/$relative_path"
  fi
  destination_path="$TARGET_ROOT/$relative_path"
  [[ -f "$destination_path" ]] || continue
  cmp -s "$source_path" "$destination_path" && continue
  previous_hash="$(manifest_hash "$relative_path")"
  current_hash="$(sha256_file "$destination_path")"
  [[ -n "$previous_hash" && "$previous_hash" == "$current_hash" ]] || CONFLICTS+=("$relative_path")
done

if (( ${#CONFLICTS[@]} > 0 )) && [[ "$FORCE" != true ]]; then
  echo "error: target contains user-modified managed files:" >&2
  printf '  %s\n' "${CONFLICTS[@]}" >&2
  echo "Rerun with --force to back them up and replace them." >&2
  exit 1
fi

if [[ "$DRY_RUN" == true ]]; then
  for relative_path in "${FILES[@]}"; do printf '%s\n' "$relative_path"; done
  printf '%s\n' ".gitignore: ensure .teamflow/"
  exit 0
fi

"$SOURCE_ROOT/scripts/setup.sh" >/dev/null

BACKUP_ROOT=""
if (( ${#CONFLICTS[@]} > 0 )); then
  BACKUP_ROOT="$TEAMFLOW_HOME/backups/$(basename "$TARGET_ROOT")-$(date -u +'%Y%m%dT%H%M%SZ')"
  for relative_path in "${CONFLICTS[@]}"; do
    mkdir -p "$BACKUP_ROOT/$(dirname "$relative_path")"
    cp -p "$TARGET_ROOT/$relative_path" "$BACKUP_ROOT/$relative_path"
  done
fi

for relative_path in "${FILES[@]}"; do
  if [[ "$relative_path" == .teamflow/server/* ]]; then
    source_path="$SOURCE_ROOT/${relative_path#.teamflow/}"
  else
    source_path="$SOURCE_ROOT/$relative_path"
  fi
  destination_path="$TARGET_ROOT/$relative_path"
  mkdir -p "$(dirname "$destination_path")"
  cp -p "$source_path" "$destination_path"
done

# Remove the previous managed location only when it is still byte-for-byte the
# file recorded by the prior manifest. User-modified files are preserved.
retired_context=".teamflow/instructions/AGENTS.md"
retired_path="$TARGET_ROOT/$retired_context"
if [[ -f "$retired_path" ]]; then
  previous_hash="$(manifest_hash "$retired_context")"
  current_hash="$(sha256_file "$retired_path")"
  if [[ -n "$previous_hash" && "$previous_hash" == "$current_hash" ]]; then
    unlink "$retired_path"
    rmdir "$TARGET_ROOT/.teamflow/instructions" 2>/dev/null || true
  else
    echo "warning: preserving user-modified retired context: $retired_context" >&2
  fi
fi
chmod +x "$TARGET_ROOT/.teamflow/bin/teamflow" "$TARGET_ROOT/.teamflow/bin/pi-runtime" \
  "$TARGET_ROOT/.teamflow/bin/memory" "$TARGET_ROOT/.teamflow/bin/memory-capture" \
  "$TARGET_ROOT/.teamflow/bin/test-patch" "$TARGET_ROOT/.teamflow/bin/server" \
  "$TARGET_ROOT/.teamflow/experiments/bin/memory-experiment" \
  "$TARGET_ROOT/.teamflow/experiments/bin/memory-compare"

if [[ ! -f "$TARGET_ROOT/.gitignore" ]]; then
  printf '# Local Teamflow runtime\n.teamflow/\n' > "$TARGET_ROOT/.gitignore"
elif ! grep -qxF '.teamflow/' "$TARGET_ROOT/.gitignore"; then
  printf '\n# Local Teamflow runtime\n.teamflow/\n' >> "$TARGET_ROOT/.gitignore"
fi

mkdir -p "$(dirname "$MANIFEST_PATH")"
node - "$SOURCE_ROOT" "$TARGET_ROOT" "$MANIFEST_PATH" "${FILES[@]}" <<'NODE'
const crypto = require("node:crypto");
const fs = require("node:fs");
const path = require("node:path");
const [sourceRoot, targetRoot, manifestPath, ...files] = process.argv.slice(2);
const hashes = {};
for (const relative of files) {
  hashes[relative] = crypto.createHash("sha256")
    .update(fs.readFileSync(path.join(targetRoot, relative))).digest("hex");
}
fs.writeFileSync(manifestPath, `${JSON.stringify({
  schema_version: 3,
  installed_at: new Date().toISOString(),
  source: "wenshiqi0/teamflow",
  files: hashes,
}, null, 2)}\n`);
NODE

LAUNCHER_DIR="${TEAMFLOW_BIN_DIR:-$HOME/.local/bin}"
LAUNCHER_PATH="$LAUNCHER_DIR/teamflow"
if [[ -f "$LAUNCHER_PATH" ]] && ! grep -q 'agent-teamflow-launcher' "$LAUNCHER_PATH"; then
  echo "warning: not replacing unrelated command: $LAUNCHER_PATH" >&2
else
  mkdir -p "$LAUNCHER_DIR"
  install -m 0755 "$SOURCE_ROOT/scripts/teamflow" "$LAUNCHER_PATH"
fi

VALIDATION_HOME="${TMPDIR:-/tmp}/agent-teamflow-init-validation"
mkdir -p "$VALIDATION_HOME"
(
  cd "$TARGET_ROOT"
  HOME="$VALIDATION_HOME" ./.teamflow/bin/teamflow debug agent planner >/dev/null
  HOME="$VALIDATION_HOME" ./.teamflow/bin/teamflow debug agent coder >/dev/null
  HOME="$VALIDATION_HOME" ./.teamflow/bin/teamflow debug agent test-writer >/dev/null
  HOME="$VALIDATION_HOME" ./.teamflow/bin/teamflow debug skill | grep -q 'basic-memory-cli'
)

echo "Teamflow installed in: $TARGET_ROOT/.teamflow"
echo "Managed manifest: $MANIFEST_PATH"
[[ -z "$BACKUP_ROOT" ]] || echo "Replaced files were backed up to: $BACKUP_ROOT"
