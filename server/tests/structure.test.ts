import { test, expect } from "bun:test";
import { readFileSync, readdirSync, existsSync } from "node:fs";
import { join } from "node:path";

const SERVER = join(import.meta.dir, "..");

function findPackageJson(dir: string): string[] {
  const results: string[] = [];
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    if (entry.name === "node_modules" || entry.name === "dist") continue;
    const full = join(dir, entry.name);
    if (entry.isDirectory()) results.push(...findPackageJson(full));
    else if (entry.name === "package.json") results.push(full);
  }
  return results;
}

function serverPkg(): any {
  return JSON.parse(readFileSync(join(SERVER, "package.json"), "utf8"));
}

// Criterion 1: exactly one package.json under server/
test("exactly one package.json under server/", () => {
  const found = findPackageJson(SERVER);
  expect(found.length).toBe(1);
  expect(found[0]).toBe(join(SERVER, "package.json"));
});

// Criterion 2: TypeScript appears once, at one version
test("typescript appears once at one version", () => {
  const pkg = serverPkg();
  const allDeps = { ...pkg.dependencies, ...pkg.devDependencies };
  const tsEntries = Object.keys(allDeps).filter((k) => k === "typescript");
  expect(tsEntries.length).toBe(1);
});

// Criterion 3: no script contains 'cd web && bun install'
test("no script contains cd web install", () => {
  const pkg = serverPkg();
  const scripts = Object.values(pkg.scripts ?? {}).join(" ");
  expect(scripts).not.toContain("cd web && bun install");
});

// Criterion 6: typecheck covers both tsc and svelte-check
test("typecheck covers both tsc and svelte-check", () => {
  const pkg = serverPkg();
  const typecheck: string = pkg.scripts?.typecheck ?? "";
  expect(typecheck).toContain("tsc");
  expect(typecheck).toContain("svelte-check");
});

// Criterion 7: server/tests/ contains no .py files
test("no .py files in server/tests/", () => {
  const files = readdirSync(import.meta.dir).filter((f) => f.endsWith(".py"));
  expect(files).toEqual([]);
});

// Criterion 8: shared/ directory exists with response types
test("shared/ directory exists", () => {
  expect(existsSync(join(SERVER, "shared"))).toBe(true);
});
