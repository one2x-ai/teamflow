import type { MemoryListResponse, MemoryNote } from "./types";

export async function fetchMemories(
  params: { page?: number; query?: string } = {},
): Promise<MemoryListResponse> {
  const searchParams = new URLSearchParams();
  if (params.page) {
    searchParams.set("page", String(params.page));
  }
  if (params.query) {
    searchParams.set("query", params.query);
  }
  const qs = searchParams.toString();
  const response = await fetch(`/api/memories${qs ? "?" + qs : ""}`);
  if (!response.ok) {
    throw new Error("failed to fetch memories");
  }
  return response.json() as Promise<MemoryListResponse>;
}

export async function fetchMemory(permalink: string): Promise<MemoryNote> {
  const response = await fetch(
    `/api/memory?permalink=${encodeURIComponent(permalink)}`,
  );
  if (!response.ok) {
    throw new Error("failed to fetch memory");
  }
  return response.json() as Promise<MemoryNote>;
}
