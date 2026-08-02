/**
 * Workspace scoping for `--dir`.
 *
 * Memories are namespaced as `teamflow/projects/<slug>/...`. When the server
 * is started with `--dir <repo>`, only that repository's namespace is shown.
 * The slug derivation must stay byte-identical to `.teamflow/bin/memory`, or
 * the same repository would resolve to two different namespaces.
 */

import fs from "node:fs";
import path from "node:path";

import { failStartup } from "../config";

export interface Workspace {
  slug: string;
  /** Permalink prefix every visible memory must start with. */
  prefix: string;
}

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

/**
 * Basename of the remote URL, lowercased, non `[a-z0-9._-]` folded to `-`,
 * trailing `-` removed. Mirrors `.teamflow/bin/memory`.
 */
export function deriveSlug(raw: string): string {
  let name = raw.includes("/") ? raw.slice(raw.lastIndexOf("/") + 1) : raw;
  if (name.endsWith(".git")) {
    name = name.slice(0, -4);
  }
  return name
    .toLowerCase()
    .replace(/[^a-z0-9._-]+/g, "-")
    .replace(/-+$/g, "");
}

/**
 * Resolve `--dir` to a workspace, failing startup rather than silently
 * falling back to the unscoped view: a wrong scope shows another
 * repository's memory, which is worse than not starting.
 */
export async function resolveWorkspace(dir: string): Promise<Workspace> {
  const resolved = path.resolve(dir);

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
    // No remote: fall back to the working tree's top-level directory name.
    const top = await runGit(resolved, ["rev-parse", "--show-toplevel"]);
    raw = top.stdout.trim();
  }

  const slug = deriveSlug(raw);
  return { slug, prefix: `teamflow/projects/${slug}/` };
}

export function inWorkspace(
  permalink: unknown,
  workspace: Workspace | null,
): boolean {
  if (workspace === null) return true;
  return typeof permalink === "string" && permalink.startsWith(workspace.prefix);
}
