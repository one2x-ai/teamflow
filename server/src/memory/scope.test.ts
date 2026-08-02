import { test, expect, describe } from "bun:test";
import { deriveSlug, inWorkspace } from "./scope";
import type { Workspace } from "./scope";

describe("deriveSlug", () => {
  test("strips git SSH remote suffix and lowercases", () => {
    expect(deriveSlug("git@github.com:one2x-ai/mcap.git")).toBe("mcap");
  });

  test("strips https remote suffix", () => {
    expect(deriveSlug("https://github.com/one2x-ai/mcap.git")).toBe("mcap");
  });

  test("handles basename without leading slash", () => {
    expect(deriveSlug("one2x-ai/mcap")).toBe("mcap");
  });

  test("keeps periods in repo name", () => {
    expect(deriveSlug("https://github.com/Foo-Bar/Baz.Qux.git")).toBe("baz.qux");
  });

  test("converts spaces to dashes", () => {
    expect(deriveSlug("git@github.com:user/Repo Name.git")).toBe("repo-name");
  });

  test("simple name unchanged", () => {
    expect(deriveSlug("simple")).toBe("simple");
  });

  test("lowercases uppercase letters", () => {
    expect(deriveSlug("UPPER")).toBe("upper");
  });

  test("strips trailing dashes", () => {
    expect(deriveSlug("trailing-dashes---")).toBe("trailing-dashes");
  });
});

describe("inWorkspace", () => {
  const ws: Workspace = { slug: "x", prefix: "teamflow/projects/x/" };

  test("returns true when permalink starts with prefix", () => {
    expect(inWorkspace("teamflow/projects/x/curated/note", ws)).toBe(true);
  });

  test("returns false when permalink has different prefix", () => {
    expect(inWorkspace("teamflow/projects/y/curated/note", ws)).toBe(false);
  });

  test("returns true when workspace is null (unscoped)", () => {
    expect(inWorkspace("anything", null)).toBe(true);
  });

  test("returns false for non-string permalink", () => {
    expect(inWorkspace(123, ws)).toBe(false);
  });

  test("returns false for undefined permalink", () => {
    expect(inWorkspace(undefined, ws)).toBe(false);
  });

  test("returns false for null permalink", () => {
    expect(inWorkspace(null, ws)).toBe(false);
  });
});
