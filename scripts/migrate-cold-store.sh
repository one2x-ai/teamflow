#!/usr/bin/env bash
# Migrate wrongly-generated cold-memory XML files from knowledge/ to state/cold-store/.
# This script is NON-DESTRUCTIVE: it reports by default and only moves with --migrate,
# always creating a backup tarball before any move. It NEVER deletes user data.
set -euo pipefail

TEAMFLOW_HOME="${TEAMFLOW_HOME:-$HOME/.teamflow}"
MEMORY_ROOT="${TEAMFLOW_MEMORY_HOME:-$TEAMFLOW_HOME/memory}"
KNOWLEDGE_DIR="$MEMORY_ROOT/knowledge"
COLD_STORE_DIR="$MEMORY_ROOT/state/cold-store"
BACKUP_DIR="$MEMORY_ROOT/state/cold-store-migration-backups"
MIGRATE=false

usage() {
  cat <<'EOF'
Usage: ./scripts/migrate-cold-store.sh [--migrate]

Find raw XML turn files wrongly written under knowledge/*/turns/ by the
pre-fix BasicMemoryAdapter, and optionally relocate them to the correct
state/cold-store/ location.

  --migrate  Move files (with backup). Default is report-only.
  -h, --help Show this help.

This script NEVER deletes files. It creates a timestamped backup tarball
before any move.
EOF
}

while (( $# > 0 )); do
  case "$1" in
    --migrate) MIGRATE=true ;;
    -h|--help) usage; exit 0 ;;
    *) echo "error: unknown argument: $1" >&2; usage >&2; exit 1 ;;
  esac
  shift
done

# Find XML files under knowledge/*/turns/
FOUND_FILES=()
if [[ -d "$KNOWLEDGE_DIR" ]]; then
  while IFS= read -r f; do
    FOUND_FILES+=("$f")
  done < <(find "$KNOWLEDGE_DIR" -path "*/turns/*" -name "*.xml" 2>/dev/null || true)
fi

if (( ${#FOUND_FILES[@]} == 0 )); then
  echo "No wrongly-generated XML turn files found under $KNOWLEDGE_DIR."
  echo "Nothing to migrate."
  exit 0
fi

echo "Found ${#FOUND_FILES[@]} XML turn file(s) under knowledge/:"
for f in "${FOUND_FILES[@]}"; do
  echo "  $f"
done

if [[ "$MIGRATE" != true ]]; then
  echo ""
  echo "Report-only mode. To relocate these files, re-run with --migrate."
  echo "A backup tarball will be created before any move."
  exit 0
fi

# Create backup before any move
TIMESTAMP="$(date -u +'%Y%m%dT%H%M%SZ')"
BACKUP_TARBALL="$BACKUP_DIR/cold-store-backup-$TIMESTAMP.tar.gz"
mkdir -p "$BACKUP_DIR"
tar -czf "$BACKUP_TARBALL" -C "$MEMORY_ROOT" \
  $(for f in "${FOUND_FILES[@]}"; do echo "${f#$MEMORY_ROOT/}"; done)
echo "Backup created: $BACKUP_TARBALL"

# Move each file to the cold-store location, preserving relative path
for f in "${FOUND_FILES[@]}"; do
  # Strip knowledge/ prefix to get the relative path
  rel="${f#$KNOWLEDGE_DIR/}"
  dest="$COLD_STORE_DIR/$rel"
  mkdir -p "$(dirname "$dest")"
  mv "$f" "$dest"
  echo "Moved: $f -> $dest"
done

echo ""
echo "Migration complete. ${#FOUND_FILES[@]} file(s) relocated."
echo "Backup: $BACKUP_TARBALL"
echo "Verify with: basic-memory status --project teamflow --local"
