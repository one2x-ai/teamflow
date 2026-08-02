import { test, expect, describe, beforeEach, afterEach } from "bun:test";
import { spawnSync } from "node:child_process";
import { join } from "node:path";
import {
  createFixture,
  httpJson,
  makeRecentItems,
  freePort,
  type Fixture,
} from "./helpers";

async function waitForExit(proc: { exitCode: number | null; killed: boolean } | null, timeoutMs = 3000): Promise<number | null> {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    if (proc && proc.exitCode !== null) return proc.exitCode;
    await new Promise((r) => setTimeout(r, 100));
  }
  return proc ? proc.exitCode : null;
}

function portRefused(port: number): boolean {
  const { spawnSync } = require("node:child_process");
  const r = spawnSync("bash", ["-c", `echo > /dev/tcp/127.0.0.1/${port}`], {
    timeout: 1000,
    stdio: "ignore",
  });
  return r.status !== 0;
}

describe("Scoped browsing – --dir <git-repo>", () => {
  let fx: Fixture;
  let scopedDir: string;

  beforeEach(async () => {
    fx = createFixture();
    fx.setRecent([
      {
        type: "note",
        title: "Recent Mcap 1",
        permalink: "teamflow/projects/mcap/curated/r1",
        file_path: "/memory/projects/mcap/curated/r1.md",
        created_at: "2025-01-01T00:00:00Z",
      },
      {
        type: "note",
        title: "Recent Other",
        permalink: "teamflow/projects/other/curated/r2",
        file_path: "/memory/projects/other/curated/r2.md",
        created_at: "2025-01-01T00:00:00Z",
      },
      {
        type: "note",
        title: "Recent Mcap 2",
        permalink: "teamflow/projects/mcap/curated/r3",
        file_path: "/memory/projects/mcap/curated/r3.md",
        created_at: "2025-01-01T00:00:00Z",
      },
    ]);
    fx.setSearch(
      [
        { title: "Mcap A", permalink: "teamflow/projects/mcap/curated/a" },
        { title: "Other B", permalink: "teamflow/projects/other/curated/b" },
        { title: "Mcap C", permalink: "teamflow/projects/mcap/curated/c" },
        { title: "Global D", permalink: "teamflow/global/curated/d" },
        { title: "Mcap E", permalink: "teamflow/projects/mcap/curated/e" },
      ],
      999,
    );
    scopedDir = join(fx.testdir, "mcap");
    spawnSync("git", ["init", "-q", scopedDir]);
    const port = freePort();
    fx.start({
      port,
      flags: ["--host", "127.0.0.1", "--port", String(port), "--dir", scopedDir],
    });
    await fx.waitReady();
  });
  afterEach(() => fx.stop());

  test("scoped no-query uses unscoped recent-activity, no --permalink", async () => {
    const { status, body } = await httpJson("GET", `${fx.baseUrl}/api/memories`);
    expect(status).toBe(200);
    const calls = fx.readLog();
    expect(calls).toContain(
      "tool recent-activity --timeframe 365d --page-size 100 --project teamflow --local",
    );
    expect(calls).not.toContain("--permalink");
    expect(calls).not.toContain("tool search-notes");
    expect(body.items.map((i: any) => i.permalink)).toEqual([
      "teamflow/projects/mcap/curated/r1",
      "teamflow/projects/mcap/curated/r3",
    ]);
  });

  test("scoped query uses unscoped search-notes, no --permalink", async () => {
    const { status, body } = await httpJson(
      "GET",
      `${fx.baseUrl}/api/memories?query=alpha%20beta`,
    );
    expect(status).toBe(200);
    expect(body.query).toBe("alpha beta");
    const calls = fx.readLog();
    expect(calls).toContain(
      "tool search-notes alpha beta --page-size 100 --project teamflow --local",
    );
    expect(calls).not.toContain("--permalink");
    expect(calls).not.toContain("tool recent-activity");
    expect(body.items.map((i: any) => i.permalink)).toEqual([
      "teamflow/projects/mcap/curated/a",
      "teamflow/projects/mcap/curated/c",
      "teamflow/projects/mcap/curated/e",
    ]);
  });

  test("no cross-project leakage: all items start with mcap prefix", async () => {
    await httpJson("GET", `${fx.baseUrl}/api/memories`);
    await httpJson("GET", `${fx.baseUrl}/api/memories?query=foo`);
    const calls = fx.readLog();
    expect(calls).not.toContain("--permalink");
    const { body } = await httpJson("GET", `${fx.baseUrl}/api/memories?query=foo`);
    for (const item of body.items) {
      expect(item.permalink.startsWith("teamflow/projects/mcap/")).toBe(true);
    }
  });

  test("scoped schema preserved: page=1, page_size=20, correct total", async () => {
    const { status, body } = await httpJson("GET", `${fx.baseUrl}/api/memories`);
    expect(status).toBe(200);
    for (const key of ["items", "page", "page_size", "total", "total_pages", "query"]) {
      expect(key in body).toBe(true);
    }
    expect(body.page).toBe(1);
    expect(body.page_size).toBe(20);
    expect(body.query).toBe("");
    expect(body.total).toBe(2);
    expect(body.total_pages).toBe(1);
    expect(body.items.length).toBe(2);

    // page=2 page_size=2 → empty (only 2 in-scope candidates)
    const { body: body2 } = await httpJson(
      "GET",
      `${fx.baseUrl}/api/memories?page=2&page_size=2`,
    );
    expect(body2.page).toBe(2);
    expect(body2.page_size).toBe(2);
    expect(body2.total).toBe(2);
    expect(body2.total_pages).toBe(1);
    expect(body2.items).toEqual([]);
  });

  test("scoped status codes preserved: 400, 404, 405", async () => {
    // invalid pagination → 400
    for (const [key, value] of [["page", "0"], ["page_size", "101"]] as [string, string][]) {
      const { status } = await httpJson(
        "GET",
        `${fx.baseUrl}/api/memories?${key}=${value}`,
      );
      expect(status).toBe(400);
    }
    // unknown path → 404
    const { status: s404 } = await httpJson("GET", `${fx.baseUrl}/no-such-path`);
    expect(s404).toBe(404);
    // POST → 405
    const { status: s405 } = await httpJson("POST", `${fx.baseUrl}/api/memories`);
    expect(s405).toBe(405);
  });

  test("scoped upstream fail returns 502", async () => {
    const failFx = createFixture();
    try {
      failFx.setRecent(makeRecentItems(1));
      failFx.setSearch([], 0);
      const failDir = join(failFx.testdir, "mcap-fail");
      spawnSync("git", ["init", "-q", failDir]);
      const port = freePort();
      failFx.start({
        port,
        flags: ["--host", "127.0.0.1", "--port", String(port), "--dir", failDir],
        envOverrides: { FAKE_BASIC_MEMORY_MODE: "fail" },
      });
      await failFx.waitReady();
      const { status } = await httpJson("GET", `${failFx.baseUrl}/api/memories`);
      expect(status).toBe(502);
    } finally {
      failFx.stop();
    }
  });

  test("scoped detail allows selected repo", async () => {
    const permalink = "teamflow/projects/mcap/curated/memory-detail";
    const encoded = encodeURIComponent(permalink);
    const { status, body } = await httpJson(
      "GET",
      `${fx.baseUrl}/api/memory?permalink=${encoded}`,
    );
    expect(status).toBe(200);
    expect(body.permalink).toBe(permalink);
    expect(fx.readLog()).toContain(
      "tool read-note teamflow/projects/mcap/curated/memory-detail",
    );
  });

  test("scoped detail rejects other repo with 403 before upstream", async () => {
    const permalink = "teamflow/projects/teamflow/curated/private-note";
    const encoded = encodeURIComponent(permalink);
    const { status, body } = await httpJson(
      "GET",
      `${fx.baseUrl}/api/memory?permalink=${encoded}`,
    );
    expect(status).toBe(403);
    expect(typeof body).toBe("object");
    const calls = fx.readLog();
    expect(calls).not.toContain(permalink);
    expect(calls).not.toContain("tool read-note");
  });
});

describe("Scoped startup validation", () => {
  test("missing --dir value fails startup", async () => {
    const fx = createFixture();
    try {
      fx.setRecent(makeRecentItems(1));
      fx.setSearch([], 0);
      const port = freePort();
      fx.start({
        port,
        flags: ["--host", "127.0.0.1", "--port", String(port), "--dir"],
      });
      const rc = await waitForExit(fx.process);
      expect(rc).not.toBeNull();
      expect(rc).not.toBe(0);
      expect(portRefused(port)).toBe(true);
    } finally {
      fx.stop();
    }
  });

  test("nonexistent dir fails startup", async () => {
    const fx = createFixture();
    try {
      fx.setRecent(makeRecentItems(1));
      fx.setSearch([], 0);
      const port = freePort();
      const nonexistent = join(fx.testdir, "does-not-exist");
      fx.start({
        port,
        flags: ["--host", "127.0.0.1", "--port", String(port), "--dir", nonexistent],
      });
      const rc = await waitForExit(fx.process);
      expect(rc).not.toBeNull();
      expect(rc).not.toBe(0);
      expect(portRefused(port)).toBe(true);
    } finally {
      fx.stop();
    }
  });

  test("non-git dir fails startup", async () => {
    const fx = createFixture();
    try {
      fx.setRecent(makeRecentItems(1));
      fx.setSearch([], 0);
      const port = freePort();
      const plainDir = join(fx.testdir, "plain-dir");
      const { mkdirSync } = require("node:fs");
      mkdirSync(plainDir, { recursive: true });
      fx.start({
        port,
        flags: ["--host", "127.0.0.1", "--port", String(port), "--dir", plainDir],
      });
      const rc = await waitForExit(fx.process);
      expect(rc).not.toBeNull();
      expect(rc).not.toBe(0);
      expect(portRefused(port)).toBe(true);
    } finally {
      fx.stop();
    }
  });
});
