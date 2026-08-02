import { test, expect, describe } from "bun:test";
import { readdirSync } from "node:fs";
import { join } from "node:path";

const SHARED_DIR = join(import.meta.dir, "..");

function collectTsFiles(dir: string): string[] {
  const entries = readdirSync(dir, { withFileTypes: true });
  const files: string[] = [];
  for (const entry of entries) {
    const fullPath = join(dir, entry.name);
    if (entry.isDirectory()) {
      if (entry.name === "__tests__") continue;
      files.push(...collectTsFiles(fullPath));
    } else if (entry.name.endsWith(".ts")) {
      files.push(fullPath);
    }
  }
  return files;
}

describe("server/shared purity", () => {
  test("shared/ directory exists and contains .ts files", () => {
    let files: string[];
    try {
      files = collectTsFiles(SHARED_DIR);
    } catch {
      throw new Error("server/shared/ directory does not exist yet");
    }
    expect(files.length).toBeGreaterThan(0);
  });

  const BUN_IMPORT = /from\s+["']bun/;
  const NODE_BUILTIN_IMPORT = /from\s+["']node:/;
  const DOM_GLOBALS = /\b(document|window|HTMLElement|localStorage|fetch)\b/g;

  const files = (() => {
    try {
      return collectTsFiles(SHARED_DIR);
    } catch {
      return [];
    }
  })();

  for (const filePath of files) {
    test(`${filePath} is type-only (no runtime imports)`, async () => {
      const content = await Bun.file(filePath).text();
      expect(BUN_IMPORT.test(content)).toBe(false);
      expect(NODE_BUILTIN_IMPORT.test(content)).toBe(false);
      DOM_GLOBALS.lastIndex = 0;
      expect(DOM_GLOBALS.test(content)).toBe(false);
    });
  }
});
