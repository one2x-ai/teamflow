#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"
TEAMFLOW_HOME="${TEAMFLOW_HOME:-$HOME/.teamflow}"

if ! command -v git >/dev/null 2>&1; then
  echo "error: git is required" >&2
  exit 1
fi

MIN_NODE_VERSION="22.19.0"
if ! command -v node >/dev/null 2>&1; then
  echo "error: Node.js $MIN_NODE_VERSION+ is required" >&2
  exit 1
fi

if ! node -e '
  const [major, minor, patch] = process.versions.node.split(".").map(Number);
  const ok = major > 22 || (major === 22 && (minor > 19 || (minor === 19 && patch >= 0)));
  process.exit(ok ? 0 : 1);
'; then
  echo "error: Node.js $MIN_NODE_VERSION+ is required; found $(node --version)" >&2
  exit 1
fi

if ! command -v npm >/dev/null 2>&1; then
  echo "error: npm is required to install Pi" >&2
  exit 1
fi

if ! command -v pi >/dev/null 2>&1; then
  echo "Installing Pi..."
  npm install --global @earendil-works/pi-coding-agent@latest
else
  CURRENT_PI_VERSION="$(pi --version)"
  if LATEST_PI_VERSION="$(npm --fetch-timeout=15000 --fetch-retries=1 view @earendil-works/pi-coding-agent version 2>/dev/null)"; then
    if [[ "$CURRENT_PI_VERSION" != "$LATEST_PI_VERSION" ]]; then
      echo "Upgrading Pi ${CURRENT_PI_VERSION} -> ${LATEST_PI_VERSION}..."
      npm install --global "@earendil-works/pi-coding-agent@${LATEST_PI_VERSION}"
    fi
  else
    echo "warning: could not check the latest Pi version; keeping ${CURRENT_PI_VERSION}" >&2
  fi
fi

if ! command -v uv >/dev/null 2>&1; then
  if ! command -v curl >/dev/null 2>&1; then
    echo "error: curl is required to install uv" >&2
    exit 1
  fi
  echo "Installing uv..."
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="$HOME/.local/bin:$PATH"
fi

UV_TOOL_BIN_DIR="$(uv tool dir --bin)"
export PATH="$UV_TOOL_BIN_DIR:$PATH"

if command -v basic-memory >/dev/null 2>&1; then
  echo "Checking for a Basic Memory upgrade..."
  BASIC_MEMORY_UPGRADE_TIMEOUT_MS="${BASIC_MEMORY_UPGRADE_TIMEOUT_MS:-120000}"
  if ! node -e '
    const { spawnSync } = require("node:child_process");
    const result = spawnSync(
      "uv",
      ["tool", "install", "--upgrade", "basic-memory"],
      { stdio: "inherit", timeout: Number(process.argv[1]) }
    );
    process.exit(result.status ?? 1);
  ' "$BASIC_MEMORY_UPGRADE_TIMEOUT_MS"; then
    echo "warning: Basic Memory upgrade check failed or timed out; keeping $(basic-memory --version)" >&2
  fi
else
  echo "Installing Basic Memory..."
  uv tool install basic-memory
fi

if [[ ! -f "$TEAMFLOW_HOME/.env" ]]; then
  mkdir -p "$TEAMFLOW_HOME"
  if [[ -f .teamflow/.env ]]; then
    cp .teamflow/.env "$TEAMFLOW_HOME/.env"
    echo "Migrated model credentials to $TEAMFLOW_HOME/.env."
  elif [[ -f .env ]]; then
    cp .env "$TEAMFLOW_HOME/.env"
    echo "Migrated model credentials to $TEAMFLOW_HOME/.env."
  else
    cp .env.example "$TEAMFLOW_HOME/.env"
    echo "Created $TEAMFLOW_HOME/.env; add the model API keys before running teamflow."
  fi
fi

LAUNCHER_DIR="${TEAMFLOW_BIN_DIR:-$HOME/.local/bin}"
LAUNCHER_PATH="$LAUNCHER_DIR/teamflow"
if [[ -f "$LAUNCHER_PATH" ]] && ! grep -q 'agent-teamflow-launcher' "$LAUNCHER_PATH"; then
  echo "warning: not replacing unrelated command: $LAUNCHER_PATH" >&2
else
  mkdir -p "$LAUNCHER_DIR"
  install -m 0755 "$ROOT_DIR/scripts/teamflow" "$LAUNCHER_PATH"
  echo "Installed teamflow launcher: $LAUNCHER_PATH"
fi

# The memory browser reads the shared cross-project store, so one global copy
# serves every project instead of being installed per project.
#
# The front end is built here, in the repository, where node_modules and the
# dev dependencies live. Only the build output is synced: the global copy has
# no node_modules, so running the build there would fail.
TEAMFLOW_HOME="${TEAMFLOW_HOME:-$HOME/.teamflow}"
SERVER_TARGET="$TEAMFLOW_HOME/server"
if [[ -d "$ROOT_DIR/server" ]]; then
  if [[ -d "$ROOT_DIR/server/web" ]]; then
    echo "Building web front end..."
    # A build failure degrades to a warning: the memory API and the CLI do not
    # depend on the front end, so bootstrap must not fail because of it.
    (cd "$ROOT_DIR/server" && bun install --registry "${NPM_REGISTRY:-https://registry.npmjs.org}" >/dev/null 2>&1 \
      && bun run build >/dev/null 2>&1) \
      || echo "warning: web front-end build failed; run 'cd server && bun install && bun run build' manually" >&2
  fi

  mkdir -p "$SERVER_TARGET"
  while IFS= read -r relative_path; do
    mkdir -p "$SERVER_TARGET/$(dirname "$relative_path")"
    install -m 0644 "$ROOT_DIR/server/$relative_path" "$SERVER_TARGET/$relative_path"
  done < <(
    cd "$ROOT_DIR/server"
    # Ship runnable sources plus the built front end. node_modules, tests, and
    # lockfiles stay in the repository; web/dist is the one build artifact the
    # global copy needs in order to serve /app.
    find . -type f ! -path './node_modules/*' ! -path './tests/*' \
      ! -path './dist/*' ! -path './web/node_modules/*' \
      ! -name 'bun.lock' ! -name 'bun.lockb' \
      ! -path '*/__pycache__/*' ! -name '*.pyc' -print | sed 's#^\./##' | sort
  )
  echo "Installed memory browser: $SERVER_TARGET"
fi

echo "Pi $(pi --version) is available."
echo "$(basic-memory --version) is available."
echo "Next: edit .env, run ./scripts/setup.sh and ./scripts/doctor.sh, then ./scripts/install.sh <target-project>."
