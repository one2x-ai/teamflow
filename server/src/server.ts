import fs from "node:fs";
import path from "node:path";

const FETCH_SIZE = 100;
const MAX_PAGE_SIZE = 100;
const DEFAULT_PAGE_SIZE = 20;

function failStartup(message: string): never {
  console.error(`teamflow server: ${message}`);
  process.exit(1);
}

const args = process.argv.slice(2);
let cliHost: string | undefined;
let cliPort: string | undefined;
let cliDir: string | undefined;
for (let index = 0; index < args.length; index += 1) {
  if (args[index] === "--host" && index + 1 < args.length) {
    cliHost = args[index + 1];
    index += 1;
  } else if (args[index] === "--port" && index + 1 < args.length) {
    cliPort = args[index + 1];
    index += 1;
  } else if (args[index] === "--dir") {
    if (index + 1 >= args.length) {
      failStartup("--dir requires a path value");
    }
    cliDir = args[index + 1];
    index += 1;
  }
}

const host = cliHost ?? process.env.TEAMFLOW_SERVER_HOST ?? "127.0.0.1";
const port = Number(cliPort ?? process.env.TEAMFLOW_SERVER_PORT ?? "7324");

const MEMORY_ROOT =
  process.env.TEAMFLOW_MEMORY_HOME ?? `${process.env.HOME}/.teamflow/memory`;
const BASIC_MEMORY_CONFIG_DIR = `${MEMORY_ROOT}/state`;
const BASIC_MEMORY_HOME = `${MEMORY_ROOT}/knowledge`;
const BASIC_MEMORY_SEMANTIC_SEARCH_ENABLED = "false";
const PROJECT = process.env.TEAMFLOW_MEMORY_PROJECT ?? "teamflow";

async function runGit(
  dir: string,
  gitArgs: string[],
): Promise<{ ok: boolean; stdout: string }> {
  const proc = Bun.spawn({
    cmd: ["git", "-C", dir, ...gitArgs],
    stdout: "pipe",
    stderr: "pipe",
    env: process.env,
  });
  const exitCode = await proc.exited;
  const stdoutText = await Bun.readableStreamToText(proc.stdout);
  return { ok: exitCode === 0, stdout: stdoutText };
}

function deriveSlug(raw: string): string {
  let name = raw.includes("/") ? raw.slice(raw.lastIndexOf("/") + 1) : raw;
  if (name.endsWith(".git")) {
    name = name.slice(0, -4);
  }
  return name
    .toLowerCase()
    .replace(/[^a-z0-9._-]+/g, "-")
    .replace(/-+$/g, "");
}

let workspaceSlug: string | null = null;
let workspacePrefix: string | null = null;

if (cliDir !== undefined) {
  const resolved = path.resolve(cliDir);
  let isDirectory = false;
  try {
    isDirectory = fs.statSync(resolved).isDirectory();
  } catch {
    isDirectory = false;
  }
  if (!isDirectory) {
    failStartup(`--dir is not an existing directory: ${resolved}`);
  }
  const workTree = await runGit(resolved, ["rev-parse", "--is-inside-work-tree"]);
  if (!workTree.ok || workTree.stdout.trim() !== "true") {
    failStartup(`--dir is not inside a git working tree: ${resolved}`);
  }
  const remote = await runGit(resolved, ["config", "--get", "remote.origin.url"]);
  let raw = remote.ok ? remote.stdout.trim() : "";
  if (raw === "") {
    const top = await runGit(resolved, ["rev-parse", "--show-toplevel"]);
    raw = top.stdout.trim();
  }
  workspaceSlug = deriveSlug(raw);
  workspacePrefix = `teamflow/projects/${workspaceSlug}/`;
}

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

async function runBasicMemory(toolArgs: string[]): Promise<unknown> {
  const proc = Bun.spawn({
    cmd: ["basic-memory", ...toolArgs],
    env: {
      ...process.env,
      BASIC_MEMORY_CONFIG_DIR,
      BASIC_MEMORY_HOME,
      BASIC_MEMORY_SEMANTIC_SEARCH_ENABLED,
    },
    stdout: "pipe",
    stderr: "pipe",
  });
  const exitCode = await proc.exited;
  const stdoutText = await Bun.readableStreamToText(proc.stdout);
  if (exitCode !== 0) {
    throw new Error(`basic-memory exited with code ${exitCode}`);
  }
  return JSON.parse(stdoutText);
}

async function handleMemories(url: URL): Promise<Response> {
  const params = url.searchParams;
  const query = params.get("query") ?? "";

  let page = 1;
  if (params.has("page")) {
    const n = Number(params.get("page"));
    if (!Number.isInteger(n) || n < 1) {
      return jsonResponse({ error: "invalid page" }, 400);
    }
    page = n;
  }

  let pageSize = DEFAULT_PAGE_SIZE;
  if (params.has("page_size")) {
    const n = Number(params.get("page_size"));
    if (!Number.isInteger(n) || n < 1 || n > MAX_PAGE_SIZE) {
      return jsonResponse({ error: "invalid page_size" }, 400);
    }
    pageSize = n;
  }

  let candidates: unknown[];
  try {
    if (query === "") {
      const parsed = await runBasicMemory([
        "tool",
        "recent-activity",
        "--timeframe",
        "365d",
        "--page-size",
        String(FETCH_SIZE),
        "--project",
        PROJECT,
        "--local",
      ]);
      if (!Array.isArray(parsed)) {
        throw new Error("recent-activity output is not a JSON array");
      }
      candidates = parsed;
    } else {
      const parsed = (await runBasicMemory([
        "tool",
        "search-notes",
        query,
        "--page-size",
        String(FETCH_SIZE),
        "--project",
        PROJECT,
        "--local",
      ])) as { results?: unknown };
      const results = parsed?.results;
      if (!Array.isArray(results)) {
        throw new Error("search-notes output has no results array");
      }
      candidates = results;
    }
  } catch {
    return jsonResponse({ error: "upstream basic-memory failure" }, 502);
  }

  if (workspacePrefix !== null) {
    const prefix = workspacePrefix;
    candidates = candidates.filter((item) => {
      if (item === null || typeof item !== "object") return false;
      const permalink = (item as { permalink?: unknown }).permalink;
      return typeof permalink === "string" && permalink.startsWith(prefix);
    });
  }

  const total = candidates.length;
  const totalPages = Math.max(1, Math.ceil(total / pageSize));
  const items = candidates.slice((page - 1) * pageSize, page * pageSize);

  return jsonResponse({
    items,
    page,
    page_size: pageSize,
    total,
    total_pages: totalPages,
    query,
  });
}

async function handleMemory(url: URL): Promise<Response> {
  const permalink = (url.searchParams.get("permalink") ?? "").trim();
  if (permalink === "") {
    return jsonResponse({ error: "missing permalink" }, 400);
  }
  if (
    workspaceSlug !== null &&
    !permalink.startsWith(`teamflow/projects/${workspaceSlug}/`)
  ) {
    return jsonResponse({ error: "memory is outside the selected workspace" }, 403);
  }

  try {
    const parsed = await runBasicMemory([
      "tool",
      "read-note",
      permalink,
      "--include-frontmatter",
      "--project",
      PROJECT,
      "--local",
    ]);
    if (parsed === null || typeof parsed !== "object" || Array.isArray(parsed)) {
      throw new Error("read-note output is not a JSON object");
    }
    const memory = parsed as Record<string, unknown>;
    if (typeof memory.content === "string") {
      memory.content = memory.content.replace(
        /^---\r?\n[\s\S]*?\r?\n---\r?\n(?:\r?\n)?/,
        "",
      );
    }
    return jsonResponse(memory);
  } catch {
    return jsonResponse({ error: "upstream basic-memory failure" }, 502);
  }
}

// --- page assembly ---------------------------------------------------------
//
// Page structure, styles, and behavior live in src/ui/<page>.{html,css,js}.
// They are read at request time and wrapped in a document shell here, so the
// server keeps serving TypeScript source directly with no build step. The
// asset text is inlined into the response rather than served as separate
// files to preserve the single-request, zero-dependency page contract.

const UI_DIR = new URL("./ui/", import.meta.url);

async function readAsset(name: string): Promise<string> {
  return await Bun.file(new URL(name, UI_DIR)).text();
}

interface PageOptions {
  page: string;
  title: string;
  substitutions?: Record<string, string>;
}

async function renderPage({ page, title, substitutions = {} }: PageOptions): Promise<Response> {
  const [body, css, js] = await Promise.all([
    readAsset(`${page}.html`),
    readAsset(`${page}.css`),
    readAsset(`${page}.js`),
  ]);

  // Server-side placeholders appear only in the html fragment and are always
  // markup this module built itself, never request or memory data.
  const resolvedBody = Object.entries(substitutions).reduce(
    (text, [key, value]) => text.replaceAll(`\${${key}}`, value),
    body,
  );

  const html = `<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>${title}</title>
<style>
${css}</style>
</head>
<body>
${resolvedBody}<script>
${js}</script>
</body>
</html>
`;

  return new Response(html, {
    status: 200,
    headers: { "content-type": "text/html; charset=utf-8" },
  });
}

function listPage(): Promise<Response> {
  const headerTitle =
    workspaceSlug === null
      ? "<h1>Teamflow Memory</h1>"
      : `<h1 data-workspace="${workspaceSlug}">Teamflow Memory <span class="workspace">(${workspaceSlug})</span></h1>`;
  return renderPage({
    page: "list",
    title: "Teamflow Memory",
    substitutions: { headerTitle },
  });
}

function detailPage(): Promise<Response> {
  const workspaceLabel = workspaceSlug === null ? "" : ` (${workspaceSlug})`;
  return renderPage({ page: "detail", title: `Memory detail${workspaceLabel}` });
}

Bun.serve({
  port,
  hostname: host,
  async fetch(req: Request): Promise<Response> {
    if (req.method !== "GET") {
      return jsonResponse({ error: "method not allowed" }, 405);
    }
    const url = new URL(req.url);
    if (url.pathname === "/health" || url.pathname === "/api/health") {
      return jsonResponse({ status: "ok" });
    }
    if (url.pathname === "/api/memories") {
      return handleMemories(url);
    }
    if (url.pathname === "/api/memory") {
      return handleMemory(url);
    }
    if (url.pathname === "/memory") {
      return detailPage();
    }
    if (url.pathname === "/") {
      return listPage();
    }
    return jsonResponse({ error: "not found" }, 404);
  },
});

console.error(
  `teamflow server listening on http://${host}:${port} (read-only, local)`,
);
