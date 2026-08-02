/**
 * Read-only memory browsing routes.
 *
 * Both endpoints keep their existing contract: query parameters, status
 * codes, and response shapes are unchanged by the Phase A refactor, because
 * the front end and its tests depend on them.
 */

import type { MemoryListResponse, MemoryNote } from "../../shared/types";
import { PAGINATION, type MemoryConfig } from "../config";
import {
  badGateway,
  badRequest,
  forbidden,
  json,
} from "../http/response";
import {
  readNote,
  recentActivity,
  searchNotes,
  stripFrontmatter,
} from "./basic-memory";
import { inWorkspace, type Workspace } from "./scope";

export interface MemoryRouteContext {
  memory: MemoryConfig;
  workspace: Workspace | null;
}

/** Positive integer within an inclusive upper bound, or null when invalid. */
export function boundedInteger(raw: string, max?: number): number | null {
  const value = Number(raw);
  if (!Number.isInteger(value) || value < 1) return null;
  if (max !== undefined && value > max) return null;
  return value;
}

export async function handleMemories(
  url: URL,
  { memory, workspace }: MemoryRouteContext,
): Promise<Response> {
  const params = url.searchParams;
  const query = params.get("query") ?? "";

  let page = 1;
  if (params.has("page")) {
    const parsed = boundedInteger(params.get("page") ?? "");
    if (parsed === null) return badRequest("invalid page");
    page = parsed;
  }

  let pageSize = PAGINATION.defaultPageSize;
  if (params.has("page_size")) {
    const parsed = boundedInteger(
      params.get("page_size") ?? "",
      PAGINATION.maxPageSize,
    );
    if (parsed === null) return badRequest("invalid page_size");
    pageSize = parsed;
  }

  let candidates: unknown[];
  try {
    candidates =
      query === ""
        ? await recentActivity(memory, PAGINATION.fetchSize)
        : await searchNotes(memory, query, PAGINATION.fetchSize);
  } catch {
    // The CLI's own message may contain local paths, so it is not forwarded.
    return badGateway();
  }

  // Scope filtering happens locally: basic-memory has no permalink-prefix
  // filter, so the fetch is broad and narrowed here.
  if (workspace !== null) {
    candidates = candidates.filter((item) => {
      if (item === null || typeof item !== "object") return false;
      return inWorkspace((item as { permalink?: unknown }).permalink, workspace);
    });
  }

  const total = candidates.length;
  const response: MemoryListResponse = {
    items: candidates.slice((page - 1) * pageSize, page * pageSize) as MemoryListResponse["items"],
    page,
    page_size: pageSize,
    total,
    total_pages: Math.max(1, Math.ceil(total / pageSize)),
    query,
  };
  return json(response);
}

export async function handleMemory(
  url: URL,
  { memory, workspace }: MemoryRouteContext,
): Promise<Response> {
  const permalink = (url.searchParams.get("permalink") ?? "").trim();
  if (permalink === "") return badRequest("missing permalink");

  // Scope is enforced here too: a direct request must not read outside the
  // selected workspace just because it skipped the list.
  if (!inWorkspace(permalink, workspace)) {
    return forbidden("memory is outside the selected workspace");
  }

  try {
    const memoryNote = (await readNote(memory, permalink)) as MemoryNote;
    if (typeof memoryNote.content === "string") {
      memoryNote.content = stripFrontmatter(memoryNote.content);
    }
    return json(memoryNote);
  } catch {
    return badGateway();
  }
}
