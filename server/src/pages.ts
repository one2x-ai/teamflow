/**
 * Page assembly for the pre-Svelte pages.
 *
 * Structure, styles, and behavior live in src/ui/<page>.{html,css,js}; they
 * are read at request time and wrapped in a document shell here, so the
 * server keeps running TypeScript source with no build step.
 *
 * Phase C replaces this module and the src/ui/ assets with the Svelte app
 * served from web/dist. It is kept intact until then so the refactor in
 * Phase A changes structure without changing behavior.
 */

import { html } from "./http/response";
import type { Workspace } from "./memory/scope";

const UI_DIR = new URL("./ui/", import.meta.url);

async function readAsset(name: string): Promise<string> {
  return await Bun.file(new URL(name, UI_DIR)).text();
}

interface PageOptions {
  page: string;
  title: string;
  substitutions?: Record<string, string>;
}

async function renderPage({
  page,
  title,
  substitutions = {},
}: PageOptions): Promise<Response> {
  const [body, css, js] = await Promise.all([
    readAsset(`${page}.html`),
    readAsset(`${page}.css`),
    readAsset(`${page}.js`),
  ]);

  // Placeholders appear only in the html fragment and are always markup this
  // module built itself, never request or memory data.
  const resolvedBody = Object.entries(substitutions).reduce(
    (text, [key, value]) => text.replaceAll(`\${${key}}`, value),
    body,
  );

  return html(`<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>${title}</title>
<style>
${css}</style>
</head>
<body>
${resolvedBody}<script>
${js}</script>
</body>
</html>
`);
}

export function listPage(workspace: Workspace | null): Promise<Response> {
  const headerTitle =
    workspace === null
      ? "<h1>Teamflow Memory</h1>"
      : `<h1 data-workspace="${workspace.slug}">Teamflow Memory <span class="workspace">(${workspace.slug})</span></h1>`;
  return renderPage({
    page: "list",
    title: "Teamflow Memory",
    substitutions: { headerTitle },
  });
}

export function detailPage(workspace: Workspace | null): Promise<Response> {
  const label = workspace === null ? "" : ` (${workspace.slug})`;
  return renderPage({ page: "detail", title: `Memory detail${label}` });
}
