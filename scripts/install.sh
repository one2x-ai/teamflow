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
    ! -path '*/tests/*' ! -path '*/__pycache__/*' ! -name '*.pyc' -print | sort
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

# Prune orphans: files the previous manifest managed that the current
# template no longer ships. Delete one only when it is still byte-for-byte
# the copy that manifest recorded, so a user-modified file is preserved and
# reported instead. This generalizes the earlier single-path retirement of
# .teamflow/instructions/AGENTS.md.
if [[ -f "$MANIFEST_PATH" ]]; then
  PREVIOUS_MANAGED=()
  while IFS= read -r relative_path; do
    [[ -n "$relative_path" ]] && PREVIOUS_MANAGED+=("$relative_path")
  done < <(
    node -e 'const m=JSON.parse(require("fs").readFileSync(process.argv[1],"utf8"));for(const k of Object.keys(m.files||{}))console.log(k)' \
      "$MANIFEST_PATH" 2>/dev/null || true
  )
  CURRENT_MANAGED=" ${FILES[*]} "
  ORPHAN_DIRS=()
  for relative_path in "${PREVIOUS_MANAGED[@]}"; do
    [[ "$CURRENT_MANAGED" == *" $relative_path "* ]] && continue
    orphan_path="$TARGET_ROOT/$relative_path"
    [[ -f "$orphan_path" ]] || continue
    previous_hash="$(manifest_hash "$relative_path")"
    current_hash="$(sha256_file "$orphan_path")"
    if [[ -n "$previous_hash" && "$previous_hash" == "$current_hash" ]]; then
      unlink "$orphan_path"
      echo "removed file no longer managed: $relative_path"
      ORPHAN_DIRS+=("$(dirname "$orphan_path")")
    else
      echo "warning: preserving user-modified file no longer managed: $relative_path" >&2
    fi
  done
  # Remove directories the pruning emptied, deepest first, stopping at
  # .teamflow itself. rmdir refuses non-empty directories, so this can never
  # delete surviving content.
  if (( ${#ORPHAN_DIRS[@]} > 0 )); then
    while IFS= read -r directory; do
      while [[ "$directory" != "$TARGET_ROOT/.teamflow" && "$directory" == "$TARGET_ROOT/.teamflow"/* ]]; do
        rmdir "$directory" 2>/dev/null || break
        directory="$(dirname "$directory")"
      done
    done < <(printf '%s\n' "${ORPHAN_DIRS[@]}" | sort -ru)
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
