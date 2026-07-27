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

function htmlPage(): Response {
  const headerTitle =
    workspaceSlug === null
      ? "<h1>Teamflow Memory</h1>"
      : `<h1 data-workspace="${workspaceSlug}">Teamflow Memory <span class="workspace">(${workspaceSlug})</span></h1>`;
  const html = `<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Teamflow Memory</title>
<style>
  :root { color-scheme: light dark; }
  * { box-sizing: border-box; }
  body {
    margin: 0;
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
    background: #0f1115;
    color: #e6e8eb;
    line-height: 1.5;
  }
  header {
    padding: 1rem 1.25rem;
    border-bottom: 1px solid #262b33;
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 0.75rem 1.5rem;
  }
  header h1 { font-size: 1.25rem; margin: 0; }
  main { max-width: 960px; margin: 0 auto; padding: 1rem 1.25rem 2.5rem; }
  form { display: flex; flex: 1 1 280px; gap: 0.5rem; }
  form input[type="search"] {
    flex: 1;
    padding: 0.5rem 0.75rem;
    border: 1px solid #3a414c;
    border-radius: 6px;
    background: #171b22;
    color: inherit;
  }
  button {
    padding: 0.5rem 0.9rem;
    border: 1px solid #3a414c;
    border-radius: 6px;
    background: #1f242d;
    color: inherit;
    cursor: pointer;
  }
  button:disabled { opacity: 0.45; cursor: default; }
  #summary { margin: 0.75rem 0; color: #9aa3af; font-size: 0.9rem; }
  .state { padding: 2rem 1rem; text-align: center; color: #9aa3af; }
  [hidden] { display: none !important; }
  #cards {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(260px, 1fr));
    gap: 0.9rem;
  }
  .card {
    border: 1px solid #262b33;
    border-radius: 8px;
    background: #171b22;
    padding: 0.9rem 1rem;
  }
  .card h2 { font-size: 1rem; margin: 0 0 0.4rem; word-break: break-word; }
  .card p { margin: 0 0 0.5rem; font-size: 0.88rem; color: #c3c9d1; word-break: break-word; }
  .card .meta { font-size: 0.78rem; color: #8b939e; display: flex; flex-direction: column; gap: 0.15rem; }
  .card a { color: #7ab3ff; text-decoration: none; word-break: break-all; }
  .card a:hover { text-decoration: underline; }
  .pager {
    display: flex;
    align-items: center;
    justify-content: center;
    gap: 1rem;
    margin-top: 1.5rem;
  }
  #page-info { color: #9aa3af; font-size: 0.9rem; }
  @media (max-width: 560px) {
    header { flex-direction: column; align-items: stretch; }
    form { flex: 1 1 auto; }
  }
</style>
</head>
<body>
<header>
  ${headerTitle}
  <form id="search-form" role="search">
    <input type="search" name="query" placeholder="Search memories" aria-label="Search memories">
    <button type="submit">Search</button>
  </form>
</header>
<main>
  <div id="summary" aria-live="polite"></div>
  <div class="state" data-state="loading" hidden>Loading memories...</div>
  <div class="state" data-state="empty" hidden>No memories found.</div>
  <div class="state" data-state="error" hidden>Failed to load memories. Please try again.</div>
  <div id="cards"></div>
  <nav class="pager" aria-label="Pagination">
    <button type="button" id="prev-btn">Previous</button>
    <span id="page-info"></span>
    <button type="button" id="next-btn">Next</button>
  </nav>
</main>
<script>
document.addEventListener("DOMContentLoaded", function () {
  var cardsEl = document.getElementById("cards");
  var summaryEl = document.getElementById("summary");
  var loadingEl = document.querySelector('[data-state="loading"]');
  var emptyEl = document.querySelector('[data-state="empty"]');
  var errorEl = document.querySelector('[data-state="error"]');
  var prevBtn = document.getElementById("prev-btn");
  var nextBtn = document.getElementById("next-btn");
  var pageInfoEl = document.getElementById("page-info");
  var formEl = document.getElementById("search-form");
  var queryInput = formEl.querySelector('input[name="query"]');

  var currentPage = 1;
  var totalPages = 1;
  var currentQuery = "";

  function setState(name) {
    loadingEl.hidden = name !== "loading";
    emptyEl.hidden = name !== "empty";
    errorEl.hidden = name !== "error";
  }

  function clearCards() {
    while (cardsEl.firstChild) {
      cardsEl.removeChild(cardsEl.firstChild);
    }
  }

  function pickText(item, keys) {
    for (var i = 0; i < keys.length; i += 1) {
      var value = item[keys[i]];
      if (typeof value === "string" && value.trim() !== "") {
        return value;
      }
    }
    return "";
  }

  function truncate(text, max) {
    if (text.length <= max) {
      return text;
    }
    return text.slice(0, max) + "...";
  }

  function addMeta(card, label, value) {
    if (typeof value !== "string" || value === "") {
      return;
    }
    var row = document.createElement("span");
    row.textContent = label + ": " + value;
    card.appendChild(row);
  }

  function renderCard(item) {
    var card = document.createElement("article");
    card.className = "card";

    var title = document.createElement("h2");
    var titleText = pickText(item, ["title"]);
    var permalink = typeof item.permalink === "string" ? item.permalink : "";
    if (permalink !== "") {
      var titleLink = document.createElement("a");
      titleLink.setAttribute("href", "/memory?permalink=" + encodeURIComponent(permalink));
      titleLink.textContent = titleText === "" ? "Untitled" : titleText;
      title.appendChild(titleLink);
    } else {
      title.textContent = titleText === "" ? "Untitled" : titleText;
    }
    card.appendChild(title);

    var excerpt = pickText(item, ["content", "body", "summary", "text"]);
    if (excerpt !== "") {
      var body = document.createElement("p");
      body.textContent = truncate(excerpt, 240);
      card.appendChild(body);
    }

    var meta = document.createElement("div");
    meta.className = "meta";
    addMeta(meta, "type", typeof item.type === "string" ? item.type : "");
    addMeta(meta, "created", typeof item.created_at === "string" ? item.created_at : "");
    card.appendChild(meta);

    return card;
  }

  function updatePager() {
    prevBtn.disabled = currentPage <= 1;
    nextBtn.disabled = currentPage >= totalPages;
    pageInfoEl.textContent = "Page " + currentPage + " of " + totalPages;
  }

  function load(page) {
    setState("loading");
    clearCards();
    var url = "/api/memories?page=1&page_size=12";
    if (page !== 1) {
      url = "/api/memories?page=" + page + "&page_size=12";
    }
    if (currentQuery !== "") {
      url += "&query=" + encodeURIComponent(currentQuery);
    }
    fetch(url)
      .then(function (res) {
        if (!res.ok) {
          throw new Error("request failed with status " + res.status);
        }
        return res.json();
      })
      .then(function (data) {
        var items = Array.isArray(data.items) ? data.items : [];
        currentPage = typeof data.page === "number" ? data.page : page;
        totalPages = typeof data.total_pages === "number" ? data.total_pages : 1;
        var total = typeof data.total === "number" ? data.total : items.length;

        if (items.length === 0) {
          summaryEl.textContent = currentQuery === ""
            ? "No recent activity"
            : 'No results for "' + currentQuery + '"';
          setState("empty");
        } else {
          summaryEl.textContent = currentQuery === ""
            ? total + " memories"
            : 'Search results for "' + currentQuery + '" (' + total + ")";
          setState("ready");
          for (var i = 0; i < items.length; i += 1) {
            cardsEl.appendChild(renderCard(items[i] || {}));
          }
        }
        updatePager();
      })
      .catch(function () {
        summaryEl.textContent = "";
        setState("error");
        updatePager();
      });
  }

  formEl.addEventListener("submit", function (event) {
    event.preventDefault();
    currentQuery = queryInput.value.trim();
    load(1);
  });

  queryInput.addEventListener("input", function () {
    if (queryInput.value.trim() === "" && currentQuery !== "") {
      currentQuery = "";
      load(1);
    }
  });

  prevBtn.addEventListener("click", function () {
    if (currentPage > 1) {
      load(currentPage - 1);
    }
  });

  nextBtn.addEventListener("click", function () {
    if (currentPage < totalPages) {
      load(currentPage + 1);
    }
  });

  load(1);
});
</script>
</body>
</html>
`;
  return new Response(html, {
    status: 200,
    headers: { "content-type": "text/html; charset=utf-8" },
  });
}

function detailPage(): Response {
  const workspaceLabel = workspaceSlug === null ? "" : ` (${workspaceSlug})`;
  const html = `<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Memory detail${workspaceLabel}</title>
<style>
  :root { color-scheme: light dark; }
  * { box-sizing: border-box; }
  body {
    margin: 0;
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
    background: #0f1115;
    color: #e6e8eb;
    line-height: 1.6;
  }
  header { padding: 1rem 1.25rem; border-bottom: 1px solid #262b33; }
  header a { color: #7ab3ff; text-decoration: none; }
  header a:hover { text-decoration: underline; }
  main { max-width: 840px; margin: 0 auto; padding: 1.5rem 1.25rem 3rem; }
  article { border: 1px solid #262b33; border-radius: 8px; background: #171b22; padding: 1.25rem; }
  h1 { margin: 0 0 1rem; font-size: 1.5rem; line-height: 1.3; }
  #detail-content { margin: 0; white-space: pre-wrap; overflow-wrap: anywhere; font: inherit; color: #cbd1d8; }
  .state { padding: 2rem 0; color: #9aa3af; }
  [hidden] { display: none !important; }
</style>
</head>
<body>
<header><a href="/">&larr; Back to memories</a></header>
<main>
  <div class="state" data-state="loading">Loading memory...</div>
  <div class="state" data-state="error" hidden>Failed to load this memory.</div>
  <article id="detail" hidden>
    <h1 id="detail-title"></h1>
    <pre id="detail-content"></pre>
  </article>
</main>
<script>
document.addEventListener("DOMContentLoaded", function () {
  var loadingEl = document.querySelector('[data-state="loading"]');
  var errorEl = document.querySelector('[data-state="error"]');
  var detailEl = document.getElementById("detail");
  var titleEl = document.getElementById("detail-title");
  var contentEl = document.getElementById("detail-content");
  var permalink = new URLSearchParams(window.location.search).get("permalink") || "";

  if (permalink === "") {
    loadingEl.hidden = true;
    errorEl.hidden = false;
    return;
  }

  fetch("/api/memory?permalink=" + encodeURIComponent(permalink))
    .then(function (res) {
      if (!res.ok) {
        throw new Error("request failed with status " + res.status);
      }
      return res.json();
    })
    .then(function (memory) {
      titleEl.textContent = typeof memory.title === "string" && memory.title !== ""
        ? memory.title
        : "Untitled";
      contentEl.textContent = typeof memory.content === "string" ? memory.content : "";
      loadingEl.hidden = true;
      detailEl.hidden = false;
    })
    .catch(function () {
      loadingEl.hidden = true;
      errorEl.hidden = false;
    });
});
</script>
</body>
</html>
`;
  return new Response(html, {
    status: 200,
    headers: { "content-type": "text/html; charset=utf-8" },
  });
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
      return htmlPage();
    }
    return jsonResponse({ error: "not found" }, 404);
  },
});

console.error(
  `teamflow server listening on http://${host}:${port} (read-only, local)`,
);
