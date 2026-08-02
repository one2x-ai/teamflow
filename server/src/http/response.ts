/**
 * Response helpers shared by every route.
 *
 * The console is read-only, so there is deliberately no helper for created,
 * accepted, or no-content responses.
 */

export function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

export function html(body: string, status = 200): Response {
  return new Response(body, {
    status,
    headers: { "content-type": "text/html; charset=utf-8" },
  });
}

export function badRequest(error: string): Response {
  return json({ error }, 400);
}

export function forbidden(error: string): Response {
  return json({ error }, 403);
}

export function notFound(error = "not found"): Response {
  return json({ error }, 404);
}

export function methodNotAllowed(): Response {
  return json({ error: "method not allowed" }, 405);
}

export function badGateway(error = "upstream basic-memory failure"): Response {
  return json({ error }, 502);
}

/**
 * A dependency is not configured or not reachable. `reason` is a stable
 * machine-readable token; `detail` is a short human-readable hint. Neither
 * ever carries credentials.
 */
export function unavailable(reason: string, detail: string): Response {
  return json({ error: "service unavailable", reason, detail }, 503);
}
