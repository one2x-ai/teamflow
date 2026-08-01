/**
 * `teamflow server` entrypoint: read-only, local-only memory and session
 * console.
 *
 * This module only assembles. Configuration lives in config.ts, routing in
 * http/, memory browsing in memory/, and the opencode reverse proxy in
 * opencode/. See docs/teamflow-web-console-design.md.
 */

import { resolveServerConfig } from "./config";
import { Router } from "./http/router";
import { json } from "./http/response";
import { handleMemories, handleMemory } from "./memory/routes";
import { resolveWorkspace, type Workspace } from "./memory/scope";
import { resolveOpencode } from "./opencode/config";
import { createOpencodeProxy, PROXY_PREFIX } from "./opencode/proxy";
import { serveAppShell, serveStatic } from "./static";

const argv = process.argv.slice(2);
const config = resolveServerConfig(argv, process.env);

// Workspace resolution can fail startup, so it runs before the listener binds:
// serving another repository's memory is worse than not starting.
const workspace: Workspace | null =
  config.dir === undefined ? null : await resolveWorkspace(config.dir);

// A missing opencode upstream is a degraded mode, not a failure: memory
// browsing does not depend on it, so only the proxy routes report it.
const opencode = resolveOpencode(argv, process.env);
const memoryContext = { memory: config.memory, workspace };

const router = new Router()
  .get(["/health", "/api/health"], () => json({ status: "ok" }))
  .get("/api/memories", (_request, url) => handleMemories(url, memoryContext))
  .get("/api/memory", (_request, url) => handleMemory(url, memoryContext))
  .prefix(PROXY_PREFIX, createOpencodeProxy(opencode))
  .prefix("/app", serveStatic)
  .get(["/", "/memory"], () => serveAppShell());

Bun.serve({
  port: config.port,
  hostname: config.host,
  fetch: (request) => router.handle(request),
});

console.error(
  `teamflow server listening on http://${config.host}:${config.port} (read-only, local)`,
);
if (!opencode.configured) {
  console.error(`teamflow server: opencode proxy disabled (${opencode.reason})`);
}
