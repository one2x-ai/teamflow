// Strip proxy env vars so localhost requests don't hang.
for (const key of ["HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"]) {
  delete process.env[key];
}

import { mkdtempSync, mkdirSync, writeFileSync, chmodSync, existsSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";
import { spawn, type ChildProcess } from "node:child_process";

const ROOT = join(import.meta.dir, "..", "..");
const SERVER_ENTRY = join(ROOT, "server", "src", "server.ts");

export function freePort(): number {
  const { Server } = require("node:net");
  const s = new Server();
  s.listen(0, "127.0.0.1");
  const port = (s.address() as any).port;
  s.close();
  return port;
}

function writeExecutable(path: string, content: string) {
  writeFileSync(path, content);
  chmodSync(path, 0o755);
}

export interface Fixture {
  testdir: string;
  bin: string;
  log: string;
  process: ChildProcess | null;
  baseUrl: string;
  start(opts: { port: number; flags?: string[]; envOverrides?: Record<string, string> }): void;
  waitReady(timeoutMs?: number): Promise<void>;
  stop(): void;
  setRecent(items: any[]): void;
  setSearch(results: any[], total: number): void;
  setDetail(detail: any): void;
  readLog(): string;
}

export function createFixture(): Fixture {
  const testdir = mkdtempSync(join(tmpdir(), "tf-server-"));
  const bin = join(testdir, "fake-bin");
  mkdirSync(bin, { recursive: true });
  const log = join(testdir, "basic-memory.log");
  const memoryHome = join(testdir, "memory");
  mkdirSync(memoryHome, { recursive: true });
  const recentFile = join(testdir, "recent.json");
  const searchFile = join(testdir, "search.json");
  const detailFile = join(testdir, "detail.json");

  // Write fake pi
  writeExecutable(join(bin, "pi"), `#!/bin/sh
if [ "\${1:-}" = "--version" ]; then
  printf '0.82.1\\n'
elif [ "\${1:-}" = "debug" ] && [ "\${2:-}" = "skill" ]; then
  printf 'plan-change\\nbasic-memory-cli\\n'
fi
exit 0
`);

  // Write fake basic-memory — byte-compatible with the Python fixture.
  writeExecutable(join(bin, "basic-memory"), `#!/bin/sh
LOG="\${FAKE_BASIC_MEMORY_LOG:-/dev/null}"
printf '%s\\n' "$*" >> "$LOG"
if [ "\${1:-} \${2:-}" = "project info" ]; then
  exit 1
fi
if [ "\${1:-}" = "status" ]; then
  printf '{}\\n'
  exit 0
fi
if [ "\${1:-} \${2:-}" = "tool read-note" ]; then
  if [ "\${FAKE_BASIC_MEMORY_MODE:-}" = "fail" ]; then
    exit 1
  fi
  if [ "\${FAKE_BASIC_MEMORY_MODE:-}" = "badjson" ]; then
    printf 'not-valid-json{{{'
    exit 0
  fi
  if [ "\${FAKE_BASIC_MEMORY_MODE:-}" = "badshape" ]; then
    printf '[]\\n'
    exit 0
  fi
  cat "\${FAKE_BASIC_MEMORY_DETAIL_FILE:?}"
  exit 0
fi
if [ "\${1:-} \${2:-}" = "tool recent-activity" ] || [ "\${1:-} \${2:-}" = "tool search-notes" ]; then
  if [ "\${FAKE_BASIC_MEMORY_MODE:-}" = "fail" ]; then
    exit 1
  fi
  if [ "\${FAKE_BASIC_MEMORY_MODE:-}" = "badjson" ]; then
    printf 'not-valid-json{{{'
    exit 0
  fi
  PAGE_SIZE=""
  PENDING=""
  for arg in "$@"; do
    if [ -n "$PENDING" ]; then
      PAGE_SIZE="$arg"
      break
    fi
    if [ "$arg" = "--page-size" ]; then
      PENDING=1
    fi
  done
  if [ -n "$PAGE_SIZE" ] && [ "$PAGE_SIZE" -gt 100 ] 2>/dev/null; then
    printf 'Error: page_size must be <= 100, got %s\\n' "$PAGE_SIZE" >&2
    exit 1
  fi
  if [ "\${1:-} \${2:-}" = "tool recent-activity" ]; then
    cat "\${FAKE_BASIC_MEMORY_RECENT_FILE:?}"
  else
    cat "\${FAKE_BASIC_MEMORY_SEARCH_FILE:?}"
  fi
  exit 0
fi
exit 0
`);

  const fixture: Fixture = {
    testdir,
    bin,
    log,
    process: null,
    baseUrl: "",

    start(opts) {
      const env: Record<string, string> = { ...process.env } as Record<string, string>;
      for (const key of Object.keys(env)) {
        if (
          key.startsWith("TEAMFLOW_") ||
          key.startsWith("WORKFLOW_") ||
          key.startsWith("OPENCODE_WORKFLOW_") ||
          key.startsWith("BASIC_MEMORY_")
        ) {
          delete env[key];
        }
      }
      env.PATH = `${bin}:${env.PATH ?? "/usr/bin:/bin"}`;
      env.HOME = join(testdir, "home");
      env.TEAMFLOW_MEMORY_HOME = memoryHome;
      env.FAKE_BASIC_MEMORY_LOG = log;
      env.FAKE_BASIC_MEMORY_RECENT_FILE = recentFile;
      env.FAKE_BASIC_MEMORY_SEARCH_FILE = searchFile;
      env.FAKE_BASIC_MEMORY_DETAIL_FILE = detailFile;
      if (opts.envOverrides) Object.assign(env, opts.envOverrides);
      const flags = opts.flags ?? ["--host", "127.0.0.1", "--port", String(opts.port)];
      fixture.process = spawn("bun", [SERVER_ENTRY, ...flags], {
        env,
        cwd: ROOT,
        stdio: ["ignore", "ignore", "ignore"],
      });
      fixture.baseUrl = `http://127.0.0.1:${opts.port}`;
    },

    async waitReady(timeoutMs = 8000) {
      const deadline = Date.now() + timeoutMs;
      while (Date.now() < deadline) {
        if (fixture.process?.exitCode !== null) break;
        try {
          const r = await fetch(`${fixture.baseUrl}/health`);
          if (r.status === 200) return;
        } catch {}
        await new Promise((r) => setTimeout(r, 200));
      }
      throw new Error(`server not ready at ${fixture.baseUrl}`);
    },

    stop() {
      if (fixture.process && fixture.process.exitCode === null) {
        fixture.process.kill("SIGTERM");
      }
    },

    setRecent(items: any[]) {
      writeFileSync(recentFile, JSON.stringify(items));
    },

    setSearch(results: any[], total: number) {
      writeFileSync(searchFile, JSON.stringify({ results, total }));
    },

    setDetail(detail: any) {
      writeFileSync(detailFile, JSON.stringify(detail));
    },

    readLog() {
      return existsSync(log) ? readFileSync(log, "utf-8") : "";
    },
  };

  // Default detail, matching the Python ServerFixture.__init__.
  fixture.setDetail({
    title: "Memory detail",
    permalink: "teamflow/projects/mcap/curated/memory-detail",
    file_path: "projects/mcap/curated/Memory detail.md",
    content: "# Memory detail\n\nReadable body.",
    frontmatter: { type: "teamflow_memory" },
  });

  return fixture;
}

export async function httpJson(method: string, url: string): Promise<{ status: number; body: any }> {
  const response = await fetch(url, { method });
  const text = await response.text();
  let body: any = {};
  try {
    body = text ? JSON.parse(text) : {};
  } catch {
    body = text;
  }
  return { status: response.status, body };
}

export async function httpGetRaw(
  url: string,
): Promise<{ status: number; contentType: string; body: string }> {
  const response = await fetch(url);
  const body = await response.text();
  return { status: response.status, contentType: response.headers.get("content-type") ?? "", body };
}

export function makeRecentItems(count: number): any[] {
  return Array.from({ length: count }, (_, i) => ({
    type: "note",
    title: `Note ${String(i).padStart(2, "0")}`,
    permalink: `/notes/${String(i).padStart(2, "0")}`,
    file_path: `/memory/notes/${String(i).padStart(2, "0")}.md`,
    created_at: "2025-01-01T00:00:00Z",
  }));
}
