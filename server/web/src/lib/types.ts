/**
 * Memory front-end types.
 *
 * These mirror the response shapes from /api/memories and /api/memory
 * (see server/src/memory/routes.ts). Fields are optional because the API
 * returns arbitrary note objects — the front end reads defensively.
 */

export interface MemoryItem {
  title?: string;
  permalink?: string;
  type?: string;
  content?: string;
  body?: string;
  summary?: string;
  text?: string;
  created_at?: string;
  [key: string]: unknown;
}

export interface MemoryListResponse {
  items: MemoryItem[];
  page: number;
  page_size: number;
  total: number;
  total_pages: number;
  query: string;
}

export interface MemoryNote {
  title?: string;
  permalink?: string;
  type?: string;
  content?: string;
  [key: string]: unknown;
}
