import { readFileSync } from "node:fs";
import { defineConfig, type Plugin } from "vite";

// Front-end iteration only. `teamflow server` never runs Vite: it reads
// src/ui/*.{html,css,js} directly under Bun, so a fresh clone and a global
// install both work with no build step.
//
//   bun run dev      # terminal 1: Bun server on 7324 (the real pages)
//   bun run ui:dev   # terminal 2: Vite on 5173, hot reload, /api proxied
//
// Vite's build output is local scratch: git-ignored, and nothing reads it.
// Do not wire it into the serving path.

/**
 * Serve each page by wrapping its real fragment in a shell, mirroring
 * renderPage() in src/server.ts. Generating the shell here keeps list.html
 * and detail.html the single source of page structure — duplicating them in
 * a checked-in index.html would let the preview drift from what ships.
 */
function previewPages(): Plugin {
  const shell = (page: string, title: string) => {
    const body = readFileSync(new URL(`./src/ui/${page}.html`, import.meta.url), "utf-8");
    return `<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>${title} (Vite preview)</title>
<link rel="stylesheet" href="/${page}.css">
</head>
<body>
${body}<script type="module" src="/${page}.js"></script>
</body>
</html>
`;
  };

  // The server substitutes ${headerTitle}; the preview has no workspace, so
  // it uses the same unscoped heading the server emits for a null slug.
  const resolve = (page: string, title: string) =>
    shell(page, title).replaceAll("${headerTitle}", "<h1>Teamflow Memory</h1>");

  return {
    name: "teamflow-preview-pages",
    configureServer(server) {
      server.middlewares.use(async (req, res, next) => {
        const route =
          req.url === "/" || req.url === "/index.html"
            ? { page: "list", title: "Teamflow Memory" }
            : req.url?.startsWith("/memory")
              ? { page: "detail", title: "Memory detail" }
              : null;
        if (route === null) return next();
        const html = await server.transformIndexHtml(
          req.url ?? "/",
          resolve(route.page, route.title),
        );
        res.setHeader("content-type", "text/html; charset=utf-8");
        res.end(html);
      });
    },
  };
}

export default defineConfig({
  root: "src/ui",
  publicDir: false,
  plugins: [previewPages()],
  server: {
    port: 5173,
    proxy: {
      "/api": {
        target: "http://127.0.0.1:7324",
        changeOrigin: true,
      },
    },
  },
});
