import { test, expect, describe, beforeEach, afterEach } from "bun:test";
import {
  createFixture,
  httpJson,
  makeRecentItems,
  freePort,
  type Fixture,
} from "./helpers";

describe("Memory API – health endpoints", () => {
  let fx: Fixture;
  beforeEach(async () => {
    fx = createFixture();
    fx.setRecent(makeRecentItems(5));
    fx.setSearch([], 0);
    const port = freePort();
    fx.start({ port });
    await fx.waitReady();
  });
  afterEach(() => fx.stop());

  test("GET /health and /api/health return 200 status ok", async () => {
    for (const path of ["/health", "/api/health"]) {
      const { status, body } = await httpJson("GET", `${fx.baseUrl}${path}`);
      expect(status).toBe(200);
      expect(body.status).toBe("ok");
    }
  });
});

describe("Memory API – memories list", () => {
  let fx: Fixture;
  beforeEach(async () => {
    fx = createFixture();
    fx.setRecent(makeRecentItems(5));
    fx.setSearch(
      [
        { title: "Alpha result", permalink: "/search/alpha" },
        { title: "Beta result", permalink: "/search/beta" },
        { title: "Gamma result", permalink: "/search/gamma" },
      ],
      999,
    );
    const port = freePort();
    fx.start({ port });
    await fx.waitReady();
  });
  afterEach(() => fx.stop());

  test("default schema has expected keys and values", async () => {
    const { status, body } = await httpJson("GET", `${fx.baseUrl}/api/memories`);
    expect(status).toBe(200);
    for (const key of ["items", "page", "page_size", "total", "total_pages", "query"]) {
      expect(key in body).toBe(true);
    }
    expect(body.page).toBe(1);
    expect(body.page_size).toBe(20);
    expect(body.query).toBe("");
    expect(body.total).toBe(5);
    expect(body.total_pages).toBe(1);
    expect(Array.isArray(body.items)).toBe(true);
    expect(body.items.length).toBe(5);
    for (const item of body.items) {
      expect("title" in item).toBe(true);
      expect("permalink" in item).toBe(true);
    }
  });

  test("pagination page=2 page_size=3 returns correct slice", async () => {
    const { status, body } = await httpJson(
      "GET",
      `${fx.baseUrl}/api/memories?page=2&page_size=3`,
    );
    expect(status).toBe(200);
    expect(body.page).toBe(2);
    expect(body.page_size).toBe(3);
    expect(body.total).toBe(5);
    expect(body.total_pages).toBe(2);
    expect(body.items.map((i: any) => i.title)).toEqual(["Note 03", "Note 04"]);
  });

  test("pagination page=2 page_size=2 returns correct slice", async () => {
    const { status, body } = await httpJson(
      "GET",
      `${fx.baseUrl}/api/memories?page=2&page_size=2`,
    );
    expect(status).toBe(200);
    expect(body.items.map((i: any) => i.title)).toEqual(["Note 02", "Note 03"]);
    expect(body.total).toBe(5);
    expect(body.total_pages).toBe(3);
  });

  test("no query invokes recent-activity with exact flags", async () => {
    await httpJson("GET", `${fx.baseUrl}/api/memories`);
    const calls = fx.readLog();
    expect(calls).toContain(
      "tool recent-activity --timeframe 365d --page-size 100 --project teamflow --local",
    );
    expect(calls).not.toContain("tool search-notes");
  });

  test("query invokes search-notes and echoes verbatim", async () => {
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
    expect(calls).not.toContain("tool recent-activity");
    // total = candidate count (3), not upstream total (999)
    expect(body.total).toBe(3);
    expect(body.total_pages).toBe(1);
    expect(body.items.length).toBe(3);
    expect(body.items.map((i: any) => i.permalink)).toEqual([
      "/search/alpha",
      "/search/beta",
      "/search/gamma",
    ]);
  });

  test("invalid pagination returns 400", async () => {
    const cases: [string, string][] = [
      ["page", "0"],
      ["page", "-1"],
      ["page", "abc"],
      ["page", ""],
      ["page_size", "0"],
      ["page_size", "101"],
      ["page_size", "9999"],
      ["page_size", "foo"],
      ["page_size", ""],
    ];
    for (const [key, value] of cases) {
      const { status, body } = await httpJson(
        "GET",
        `${fx.baseUrl}/api/memories?${key}=${value}`,
      );
      expect(status).toBe(400);
      expect(typeof body).toBe("object");
    }
  });

  test("write methods return 405", async () => {
    for (const method of ["POST", "PUT", "DELETE"]) {
      for (const path of ["/api/memories", "/health"]) {
        const { status, body } = await httpJson(method, `${fx.baseUrl}${path}`);
        expect(status).toBe(405);
        expect(typeof body).toBe("object");
      }
    }
  });

  test("unknown path returns 404", async () => {
    const { status, body } = await httpJson("GET", `${fx.baseUrl}/no-such-path`);
    expect(status).toBe(404);
    expect(typeof body).toBe("object");
  });
});

describe("Memory API – upstream failures", () => {
  for (const mode of ["fail", "badjson"]) {
    test(`mode=${mode} returns 502`, async () => {
      const fx = createFixture();
      try {
        fx.setRecent(makeRecentItems(2));
        fx.setSearch([], 0);
        const port = freePort();
        fx.start({ port, envOverrides: { FAKE_BASIC_MEMORY_MODE: mode } });
        await fx.waitReady();
        const { status, body } = await httpJson("GET", `${fx.baseUrl}/api/memories`);
        expect(status).toBe(502);
        expect(typeof body).toBe("object");
      } finally {
        fx.stop();
      }
    });
  }
});

describe("Memory Detail API", () => {
  const PERMALINK = "teamflow/projects/mcap/curated/memory-detail";
  let fx: Fixture;

  beforeEach(async () => {
    fx = createFixture();
    const item = { title: "Readable memory", permalink: PERMALINK };
    fx.setRecent([item]);
    fx.setSearch([item], 1);
    fx.setDetail({
      title: "Readable memory",
      permalink: PERMALINK,
      file_path: "projects/mcap/curated/Readable memory.md",
      content:
        "---\npermalink: teamflow/projects/mcap/curated/memory-detail\n---\n\n# Readable memory\n\nFull detail body.",
      frontmatter: { type: "teamflow_memory", tags: ["mcap"] },
    });
    const port = freePort();
    fx.start({ port });
    await fx.waitReady();
  });
  afterEach(() => fx.stop());

  test("detail reads note with exact flags and strips frontmatter", async () => {
    const encoded = encodeURIComponent(PERMALINK);
    const { status, body } = await httpJson(
      "GET",
      `${fx.baseUrl}/api/memory?permalink=${encoded}`,
    );
    expect(status).toBe(200);
    expect(body.title).toBe("Readable memory");
    expect(body.content).toBe("# Readable memory\n\nFull detail body.");
    expect(body.content).not.toContain("teamflow/projects/mcap/");
    const calls = fx.readLog();
    expect(calls).toContain(
      "tool read-note teamflow/projects/mcap/curated/memory-detail --include-frontmatter --project teamflow --local",
    );
  });

  test("missing permalink returns 400", async () => {
    const { status, body } = await httpJson("GET", `${fx.baseUrl}/api/memory`);
    expect(status).toBe(400);
    expect(typeof body).toBe("object");
    const calls = fx.readLog();
    expect(calls).not.toContain("tool read-note");
  });

  for (const mode of ["fail", "badjson", "badshape"]) {
    test(`upstream ${mode} returns 502`, async () => {
      const failFx = createFixture();
      try {
        failFx.setRecent([]);
        failFx.setSearch([], 0);
        const port = freePort();
        failFx.start({ port, envOverrides: { FAKE_BASIC_MEMORY_MODE: mode } });
        await failFx.waitReady();
        const encoded = encodeURIComponent(PERMALINK);
        const { status, body } = await httpJson(
          "GET",
          `${failFx.baseUrl}/api/memory?permalink=${encoded}`,
        );
        expect(status).toBe(502);
        expect(typeof body).toBe("object");
      } finally {
        failFx.stop();
      }
    });
  }
});
