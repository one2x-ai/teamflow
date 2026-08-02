import { test, expect, describe, beforeAll, afterAll } from "bun:test";
import { spawnSync } from "node:child_process";
import { existsSync, readFileSync, readdirSync } from "node:fs";
import { join } from "node:path";
import { createFixture, httpGetRaw, freePort, type Fixture } from "./helpers";

const SERVER = join(import.meta.dir, "..");
const WEB = join(SERVER, "web");
const DIST = join(WEB, "dist");

// Build env explicitly includes proxy so `bun install` can reach the registry.
const buildEnv: Record<string, string> = {
  ...(process.env as Record<string, string>),
  HTTP_PROXY: "http://127.0.0.1:1087",
  HTTPS_PROXY: "http://127.0.0.1:1087",
  ALL_PROXY: "socks5://127.0.0.1:1080",
};

function runBuild(): void {
  spawnSync("bun", ["run", "build"], {
    cwd: SERVER,
    env: buildEnv,
    timeout: 300_000,
  });
}

function extractAssetUrls(html: string): string[] {
  const matches = html.match(/(?:src|href)="([^"]+\.(?:js|css))"/g) ?? [];
  return matches
    .map((s) => s.match(/"([^"]+)"/)?.[1] ?? "")
    .filter(Boolean);
}

describe("Build output", () => {
  beforeAll(() => {
    runBuild();
  });

  test("build produces dist/index.html", () => {
    expect(existsSync(join(DIST, "index.html"))).toBe(true);
  });

  test("build produces hashed assets", () => {
    const assetsDir = join(DIST, "assets");
    expect(existsSync(assetsDir)).toBe(true);
    const files = readdirSync(assetsDir);
    expect(files.length).toBeGreaterThan(0);
    const hashed = files.filter((f) => /[a-f0-9]{6,}/.test(f));
    expect(hashed.length).toBeGreaterThan(0);
  });

  test("index.html asset URLs start with /app/", () => {
    const html = readFileSync(join(DIST, "index.html"), "utf-8");
    const urls = extractAssetUrls(html);
    expect(urls.length).toBeGreaterThan(0);
    for (const url of urls) {
      expect(url.startsWith("/app/")).toBe(true);
    }
  });
});

describe("Static HTTP serving", () => {
  let fx: Fixture;
  let cssName: string | undefined;
  let jsName: string | undefined;

  beforeAll(async () => {
    if (!existsSync(join(DIST, "index.html"))) {
      runBuild();
    }
    const assetsDir = join(DIST, "assets");
    if (existsSync(assetsDir)) {
      const files = readdirSync(assetsDir);
      cssName = files.find((f) => f.endsWith(".css"));
      jsName = files.find((f) => f.endsWith(".js"));
    }
    fx = createFixture();
    fx.setRecent([]);
    fx.setSearch([], 0);
    const port = freePort();
    fx.start({ port });
    await fx.waitReady();
  });

  afterAll(() => fx.stop());

  test("GET /app returns HTML", async () => {
    const { status, contentType, body } = await httpGetRaw(`${fx.baseUrl}/app`);
    expect(status).toBe(200);
    expect(contentType.toLowerCase()).toContain("text/html");
    expect(body.toLowerCase()).toContain("<html");
    expect(body).toContain('<div id="app">');
  });

  test("CSS asset served with text/css", async () => {
    expect(cssName).toBeDefined();
    const { status, contentType } = await httpGetRaw(
      `${fx.baseUrl}/app/assets/${cssName}`,
    );
    expect(status).toBe(200);
    expect(contentType.toLowerCase()).toContain("text/css");
  });

  test("JS asset served with javascript type", async () => {
    expect(jsName).toBeDefined();
    const { status, contentType } = await httpGetRaw(
      `${fx.baseUrl}/app/assets/${jsName}`,
    );
    expect(status).toBe(200);
    expect(contentType.toLowerCase()).toContain("javascript");
  });

  test("SPA fallback: /app and /app/some/deep/path return identical HTML", async () => {
    const { body: rootBody } = await httpGetRaw(`${fx.baseUrl}/app`);
    const { body: deepBody } = await httpGetRaw(`${fx.baseUrl}/app/some/deep/path`);
    expect(rootBody).toBe(deepBody);
  });

  test("missing asset under /app/assets/ returns 404", async () => {
    const { status } = await httpGetRaw(`${fx.baseUrl}/app/assets/missing.js`);
    expect(status).toBe(404);
  });

  test("GET / serves app shell", async () => {
    const { status, contentType, body } = await httpGetRaw(`${fx.baseUrl}/`);
    expect(status).toBe(200);
    expect(contentType.toLowerCase()).toContain("text/html");
    expect(body).toContain('<div id="app">');
    expect(body).toContain("/app/assets/");
  });

  test("all declared assets in served index.html are reachable", async () => {
    const { body } = await httpGetRaw(`${fx.baseUrl}/app`);
    const declared = extractAssetUrls(body);
    expect(declared.length).toBeGreaterThan(0);
    for (const url of declared) {
      expect(url.startsWith("/app/")).toBe(true);
      const { status } = await httpGetRaw(`${fx.baseUrl}${url}`);
      expect(status).toBe(200);
    }
  });
});

describe("Typecheck", () => {
  test("bun run typecheck exits 0", () => {
    if (!existsSync(join(DIST, "index.html"))) {
      runBuild();
    }
    const result = spawnSync("bun", ["run", "typecheck"], {
      cwd: SERVER,
      env: buildEnv,
      timeout: 120_000,
    });
    expect(result.status).toBe(0);
  });
});
