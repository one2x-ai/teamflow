/**
 * Static asset server for the built Svelte front end.
 *
 * Serves files from web/dist/ with SPA fallback. The serving path is
 * build-free: it reads prebuilt files with Bun.file(), never invokes Vite.
 * See docs/teamflow-web-console-design.md, Phase B.
 */

import { notFound } from "./http/response";

const DIST_DIR = new URL("../web/dist/", import.meta.url);

const MIME_TYPES: Record<string, string> = {
  ".html": "text/html; charset=utf-8",
  ".js": "text/javascript",
  ".mjs": "text/javascript",
  ".css": "text/css",
  ".svg": "image/svg+xml",
  ".png": "image/png",
  ".jpg": "image/jpeg",
  ".ico": "image/x-icon",
  ".woff": "font/woff",
  ".woff2": "font/woff2",
  ".json": "application/json",
  ".map": "application/json",
};

function mimeType(pathname: string): string {
  const dot = pathname.lastIndexOf(".");
  if (dot === -1) return "application/octet-stream";
  return MIME_TYPES[pathname.substring(dot).toLowerCase()] ?? "application/octet-stream";
}

/**
 * Serve the built SPA from web/dist/. Mounted at the /app prefix.
 *
 * - Real files are served with the correct content-type.
 * - Missing files under /assets/ return 404 (not the SPA fallback).
 * - Any other missing path falls back to index.html for client-side routing.
 */
export function serveStatic(
  _request: Request,
  url: URL,
): Response | Promise<Response> {
  const prefix = "/app";
  const pathname = url.pathname;

  // Guard: only /app and /app/*, not /application or similar.
  if (pathname !== prefix && !pathname.startsWith(prefix + "/")) {
    return notFound();
  }

  let relative = pathname.substring(prefix.length);
  if (relative === "" || relative === "/") {
    relative = "/index.html";
  }

  // Reject path traversal — all segments must be simple names.
  if (relative.includes("..")) {
    return notFound();
  }

  const fileUrl = new URL("." + relative, DIST_DIR);
  // Pass the content type into Bun.file itself: when a BunFile is used as a
  // Response body, Bun takes the content-type from file.type and ignores the
  // Response headers, so the type must be attached at file construction.
  const file = Bun.file(fileUrl, { type: mimeType(relative) });

  return file.exists().then((exists) => {
    if (exists) {
      return new Response(file);
    }

    // Explicit asset requests must 404, not fall back to the SPA shell.
    if (relative.startsWith("/assets/")) {
      return notFound();
    }

    // SPA fallback: serve index.html for client-side routing paths.
    const index = Bun.file(new URL("./index.html", DIST_DIR), {
      type: "text/html; charset=utf-8",
    });
    return new Response(index);
  });
}

/**
 * Serve the built SPA shell (index.html) for client-side route entry points
 * like GET / and GET /memory. The SPA fallback in serveStatic handles /app/*
 * deep links; this serves the shell directly at the root.
 */
export async function serveAppShell(): Promise<Response> {
  const index = Bun.file(new URL("./index.html", DIST_DIR), {
    type: "text/html; charset=utf-8",
  });
  return new Response(index);
}
