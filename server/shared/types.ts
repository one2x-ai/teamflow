/**
 * Shared API response types used by both the backend (src/) and the
 * front end (web/). This module must stay dependency-free so it works
 * in both the Bun server context and the Vite/browser build.
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
