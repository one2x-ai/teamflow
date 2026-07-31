/**
 * basic-memory CLI adapter.
 *
 * Every read goes through the locally installed `basic-memory` binary with
 * `--local`: no MCP, no cloud, no account. The environment is pinned to the
 * shared store so the console never depends on the caller's shell setup.
 */

import type { MemoryConfig } from "../config";

export class BasicMemoryError extends Error {}

export async function runBasicMemory(
  config: MemoryConfig,
  toolArgs: string[],
): Promise<unknown> {
  const proc = Bun.spawn({
    cmd: ["basic-memory", ...toolArgs],
    env: {
      ...process.env,
      BASIC_MEMORY_CONFIG_DIR: config.configDir,
      BASIC_MEMORY_HOME: config.home,
      BASIC_MEMORY_SEMANTIC_SEARCH_ENABLED: config.semanticSearchEnabled,
    },
    stdout: "pipe",
    stderr: "pipe",
  });
  const exitCode = await proc.exited;
  const stdoutText = await Bun.readableStreamToText(proc.stdout);
  if (exitCode !== 0) {
    throw new BasicMemoryError(`basic-memory exited with code ${exitCode}`);
  }
  return JSON.parse(stdoutText);
}

export async function recentActivity(
  config: MemoryConfig,
  pageSize: number,
): Promise<unknown[]> {
  const parsed = await runBasicMemory(config, [
    "tool",
    "recent-activity",
    "--timeframe",
    "365d",
    "--page-size",
    String(pageSize),
    "--project",
    config.project,
    "--local",
  ]);
  if (!Array.isArray(parsed)) {
    throw new BasicMemoryError("recent-activity output is not a JSON array");
  }
  return parsed;
}

export async function searchNotes(
  config: MemoryConfig,
  query: string,
  pageSize: number,
): Promise<unknown[]> {
  const parsed = (await runBasicMemory(config, [
    "tool",
    "search-notes",
    query,
    "--page-size",
    String(pageSize),
    "--project",
    config.project,
    "--local",
  ])) as { results?: unknown };
  const results = parsed?.results;
  if (!Array.isArray(results)) {
    throw new BasicMemoryError("search-notes output has no results array");
  }
  return results;
}

export async function readNote(
  config: MemoryConfig,
  permalink: string,
): Promise<Record<string, unknown>> {
  const parsed = await runBasicMemory(config, [
    "tool",
    "read-note",
    permalink,
    "--include-frontmatter",
    "--project",
    config.project,
    "--local",
  ]);
  if (parsed === null || typeof parsed !== "object" || Array.isArray(parsed)) {
    throw new BasicMemoryError("read-note output is not a JSON object");
  }
  return parsed as Record<string, unknown>;
}

/**
 * Drop the YAML frontmatter block so the UI renders prose, not metadata.
 * Frontmatter is still requested from the CLI because the reader needs the
 * note's type to decide how to present it.
 */
export function stripFrontmatter(content: string): string {
  return content.replace(/^---\r?\n[\s\S]*?\r?\n---\r?\n(?:\r?\n)?/, "");
}
