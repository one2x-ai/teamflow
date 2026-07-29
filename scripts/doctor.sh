#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"
TEAMFLOW_HOME="${TEAMFLOW_HOME:-$HOME/.teamflow}"
MEMORY_ROOT="${TEAMFLOW_MEMORY_HOME:-$TEAMFLOW_HOME/memory}"
export BASIC_MEMORY_AUTO_UPDATE=false
export BASIC_MEMORY_CONFIG_DIR="$MEMORY_ROOT/state"
export BASIC_MEMORY_HOME="$MEMORY_ROOT/knowledge"
export BASIC_MEMORY_SEMANTIC_SEARCH_ENABLED=false

ERRORS=0
DOCTOR_HOME="${TMPDIR:-/tmp}/agent-teamflow-doctor"
MIN_PI_VERSION="0.82.1"
MIN_BASIC_MEMORY_VERSION="0.22.1"
MEMORY_PROJECT_NAME="${TEAMFLOW_MEMORY_PROJECT:-${BASIC_MEMORY_PROJECT:-teamflow}}"
mkdir -p "$DOCTOR_HOME"

pass() {
  printf 'ok: %s\n' "$1"
}

fail() {
  printf 'error: %s\n' "$1" >&2
  ERRORS=$((ERRORS + 1))
}

if command -v git >/dev/null 2>&1; then
  pass "git $(git --version | awk '{print $3}')"
else
  fail "git is not installed"
fi

if command -v node >/dev/null 2>&1; then
  NODE_MAJOR="$(node -p 'process.versions.node.split(".")[0]')"
  if (( NODE_MAJOR >= 20 )); then
    pass "node $(node --version)"
  else
    fail "Node.js 20+ is required; found $(node --version)"
  fi
else
  fail "node is not installed"
fi

if command -v pi >/dev/null 2>&1; then
  PI_VERSION="$(pi --version)"
  if node -e '
    const parse = (value) => value.split(".").map(Number);
    const current = parse(process.argv[1]);
    const minimum = parse(process.argv[2]);
    const valid = [0, 1, 2].every((_, index) =>
      current[index] === minimum[index] ||
      current.slice(0, index).some((value, prior) => value > minimum[prior]) ||
      current[index] > minimum[index]
    );
    process.exit(valid ? 0 : 1);
  ' "$PI_VERSION" "$MIN_PI_VERSION"; then
    pass "pi $PI_VERSION"
  else
    fail "Pi $MIN_PI_VERSION+ is required; found $PI_VERSION"
  fi
else
  fail "pi is not installed; run ./scripts/bootstrap.sh"
fi

if command -v basic-memory >/dev/null 2>&1; then
  BASIC_MEMORY_VERSION="$(basic-memory --version | awk '{print $NF}')"
  if node -e '
    const parse = (value) => value.split(".").map(Number);
    const current = parse(process.argv[1]);
    const minimum = parse(process.argv[2]);
    for (let index = 0; index < 3; index += 1) {
      if ((current[index] || 0) > (minimum[index] || 0)) process.exit(0);
      if ((current[index] || 0) < (minimum[index] || 0)) process.exit(1);
    }
    process.exit(0);
  ' "$BASIC_MEMORY_VERSION" "$MIN_BASIC_MEMORY_VERSION"; then
    pass "basic-memory $BASIC_MEMORY_VERSION"
  else
    fail "Basic Memory $MIN_BASIC_MEMORY_VERSION+ is required; found $BASIC_MEMORY_VERSION"
  fi
  if basic-memory project info "$MEMORY_PROJECT_NAME" --local --json >/dev/null 2>&1 && \
     basic-memory status --project "$MEMORY_PROJECT_NAME" --local --json | \
       node -e 'let s=""; process.stdin.on("data", d => s += d).on("end", () => {
         const status = JSON.parse(s);
         const pending = (status.new?.length || 0) + (status.modified?.length || 0) +
           (status.deleted?.length || 0) + Object.keys(status.moves || {}).length;
         process.exit(pending === 0 ? 0 : 1);
       });'; then
    pass "local Basic Memory project $MEMORY_PROJECT_NAME is ready"
  else
    fail "local Basic Memory is not initialized; run ./scripts/setup"
  fi
  if [[ -d "$BASIC_MEMORY_HOME" && -f "$BASIC_MEMORY_CONFIG_DIR/config.json" ]]; then
    pass "Basic Memory shared store is under $MEMORY_ROOT"
  else
    fail "shared Basic Memory paths are missing; run ./scripts/setup"
  fi
else
  fail "basic-memory is not installed; run ./scripts/bootstrap.sh"
fi

if node -e 'JSON.parse(require("fs").readFileSync(".teamflow/models.json", "utf8"))' >/dev/null 2>&1; then
  pass "pi models configuration is valid JSON"
else
  fail ".teamflow/models.json is not valid JSON"
fi

if python3 .teamflow/skills/implement-change/scripts/source_safety.py --self-test >/dev/null && \
   python3 .teamflow/skills/implement-change/scripts/source_safety.py README.md >/dev/null; then
  pass "source control-byte gate is operational"
else
  fail "source control-byte gate failed on README.md"
fi

if python3 .teamflow/skills/plan-change/scripts/phase_state.py --help >/dev/null; then
  pass "code phase receipts are operational"
else
  fail "code phase receipt helper is unavailable"
fi

INHERITED_KIMI_API_KEY="${KIMI_API_KEY:-}"
INHERITED_ZHIPU_API_KEY="${ZHIPU_API_KEY:-}"
INHERITED_MIMO_API_KEY="${MIMO_API_KEY:-}"
INHERITED_DEEPSEEK_API_KEY="${DEEPSEEK_API_KEY:-}"
LOADED_ENV=false
for env_file in "$TEAMFLOW_HOME/.env" .teamflow/.env; do
  if [[ ! -f "$env_file" ]]; then continue; fi
  LOADED_ENV=true
  set -a
  # shellcheck disable=SC1091
  source "$env_file"
  set +a
done
if [[ -n "$INHERITED_KIMI_API_KEY" ]]; then export KIMI_API_KEY="$INHERITED_KIMI_API_KEY"; fi
if [[ -n "$INHERITED_ZHIPU_API_KEY" ]]; then export ZHIPU_API_KEY="$INHERITED_ZHIPU_API_KEY"; fi
if [[ -n "$INHERITED_MIMO_API_KEY" ]]; then export MIMO_API_KEY="$INHERITED_MIMO_API_KEY"; fi
if [[ -n "$INHERITED_DEEPSEEK_API_KEY" ]]; then export DEEPSEEK_API_KEY="$INHERITED_DEEPSEEK_API_KEY"; fi
if [[ "$LOADED_ENV" != true && ( -z "${KIMI_API_KEY:-}" || -z "${ZHIPU_API_KEY:-}" || -z "${MIMO_API_KEY:-}" || -z "${DEEPSEEK_API_KEY:-}" ) ]]; then
  fail "$TEAMFLOW_HOME/.env is missing; copy .env.example there"
fi

if [[ -n "${KIMI_API_KEY:-}" ]]; then
  pass "KIMI_API_KEY is set"
else
  fail "KIMI_API_KEY is empty"
fi

if [[ -n "${ZHIPU_API_KEY:-}" ]]; then
  pass "ZHIPU_API_KEY is set"
else
  fail "ZHIPU_API_KEY is empty"
fi

if [[ -n "${MIMO_API_KEY:-}" ]]; then
  pass "MIMO_API_KEY is set"
else
  fail "MIMO_API_KEY is empty"
fi

if [[ -n "${DEEPSEEK_API_KEY:-}" ]]; then
  pass "DEEPSEEK_API_KEY is set"
else
  fail "DEEPSEEK_API_KEY is empty"
fi

for runtime_command in teamflow memory memory-capture test-patch server; do
  if [[ -x ".teamflow/bin/$runtime_command" ]]; then
    pass "runtime command $runtime_command is executable"
  else
    fail "runtime command $runtime_command is missing or not executable"
  fi
done

for experiment_command in memory-experiment memory-compare; do
  if [[ -x ".teamflow/experiments/bin/$experiment_command" ]]; then
    pass "experimental command $experiment_command is isolated from the public bin"
  else
    fail "experimental command $experiment_command is missing or not executable"
  fi
done

if [[ -f ".teamflow/extensions/teamflow-task/index.ts" ]]; then
  pass "task role-launcher extension is installed"
else
  fail "task role-launcher extension .teamflow/extensions/teamflow-task/index.ts is missing"
fi

if [[ -f ".teamflow/extensions/memory-context/index.ts" ]]; then
  pass "memory-context observation extension is installed"
else
  fail "memory-context observation extension .teamflow/extensions/memory-context/index.ts is missing"
fi

if [[ -f ".teamflow/extensions/memory-context/turn-block.ts" ]]; then
  pass "cold-memory turn-block module is installed"
else
  fail "cold-memory turn-block module .teamflow/extensions/memory-context/turn-block.ts is missing"
fi

if [[ -f ".teamflow/extensions/memory-context/cold-memory-store.ts" ]]; then
  pass "cold-memory-store interface module is installed"
else
  fail "cold-memory-store interface module .teamflow/extensions/memory-context/cold-memory-store.ts is missing"
fi

if [[ -f ".teamflow/extensions/memory-context/file-cold-store.ts" ]]; then
  pass "cold-memory file-cold-store module is installed"
else
  fail "cold-memory file-cold-store module .teamflow/extensions/memory-context/file-cold-store.ts is missing"
fi

if [[ -f ".teamflow/extensions/memory-context/rule-cache.ts" ]]; then
  pass "rule-cache module is installed"
else
  fail "rule-cache module .teamflow/extensions/memory-context/rule-cache.ts is missing"
fi

if [[ -f ".teamflow/extensions/memory-context/rule-cache-reducer.ts" ]]; then
  pass "rule-cache-reducer module is installed"
else
  fail "rule-cache-reducer module .teamflow/extensions/memory-context/rule-cache-reducer.ts is missing"
fi

if [[ -f ".teamflow/extensions/memory-context/turn-index.ts" ]]; then
  pass "turn-index module is installed"
else
  fail "turn-index module .teamflow/extensions/memory-context/turn-index.ts is missing"
fi

if [[ -f ".teamflow/settings.json" ]] && \
   grep -q '"enabled"[[:space:]]*:[[:space:]]*false' .teamflow/settings.json; then
  pass "compaction is disabled via .teamflow/settings.json"
else
  fail ".teamflow/settings.json with compaction.enabled=false is missing"
fi

for agent in planner command coder test-writer test-runner supervisor emotional-salience-sensor memory-compressor memory-extractor memory-formatter memory-indexer; do
  if HOME="$DOCTOR_HOME" ./.teamflow/bin/teamflow debug agent "$agent" >/dev/null 2>&1; then
    pass "agent $agent is discoverable"
  else
    fail "agent $agent is not discoverable"
  fi
done

SKILL_OUTPUT="$(HOME="$DOCTOR_HOME" ./.teamflow/bin/teamflow debug skill 2>/dev/null)"
if grep -q 'plan-change' <<<"$SKILL_OUTPUT" && \
   grep -q 'basic-memory-cli' <<<"$SKILL_OUTPUT" && \
   grep -q 'memory-notes' <<<"$SKILL_OUTPUT" && \
   grep -q 'memory-capture' <<<"$SKILL_OUTPUT" && \
   grep -q 'memory-recall' <<<"$SKILL_OUTPUT" && \
   grep -q 'observe-inner-loop' <<<"$SKILL_OUTPUT" && \
   grep -q 'memory-curate' <<<"$SKILL_OUTPUT" && \
   grep -q 'extract-memory' <<<"$SKILL_OUTPUT" && \
   grep -q 'detect-emotional-salience' <<<"$SKILL_OUTPUT"; then
  pass "project skills are discoverable"
else
  fail "project skills are not discoverable"
fi

if (( ERRORS > 0 )); then
  printf '\nDoctor found %d problem(s).\n' "$ERRORS" >&2
  exit 1
fi

echo "All local checks passed."
