/**
 * Minimal request router.
 *
 * Routes are matched in registration order: exact pathname first, then
 * prefix routes (used by the opencode proxy, which owns a whole subtree).
 * Only GET is dispatched by default because the console is read-only; a
 * route may opt into other methods explicitly.
 */

import { methodNotAllowed, notFound } from "./response";

export type Handler = (
  request: Request,
  url: URL,
) => Response | Promise<Response>;

interface ExactRoute {
  kind: "exact";
  pathname: string;
  methods: Set<string>;
  handler: Handler;
}

interface PrefixRoute {
  kind: "prefix";
  prefix: string;
  methods: Set<string> | "any";
  handler: Handler;
}

type Route = ExactRoute | PrefixRoute;

export class Router {
  private readonly routes: Route[] = [];

  get(pathname: string | string[], handler: Handler): this {
    const names = Array.isArray(pathname) ? pathname : [pathname];
    for (const name of names) {
      this.routes.push({
        kind: "exact",
        pathname: name,
        methods: new Set(["GET"]),
        handler,
      });
    }
    return this;
  }

  /**
   * Claim an entire path subtree. `methods: "any"` lets the proxy forward
   * verbs transparently instead of the console deciding for the upstream.
   */
  prefix(
    prefix: string,
    handler: Handler,
    methods: Set<string> | "any" = "any",
  ): this {
    this.routes.push({ kind: "prefix", prefix, methods, handler });
    return this;
  }

  async handle(request: Request): Promise<Response> {
    const url = new URL(request.url);

    for (const route of this.routes) {
      if (route.kind === "prefix") {
        if (!url.pathname.startsWith(route.prefix)) continue;
        if (route.methods !== "any" && !route.methods.has(request.method)) {
          return methodNotAllowed();
        }
        return route.handler(request, url);
      }
      if (route.pathname !== url.pathname) continue;
      if (!route.methods.has(request.method)) return methodNotAllowed();
      return route.handler(request, url);
    }

    // An unmatched non-GET request is a method problem on the read-only
    // surface, not a missing resource.
    if (request.method !== "GET") return methodNotAllowed();
    return notFound();
  }
}
