/**
 * Reverse proxy for the opencode API.
 *
 * The browser sees a single origin (the console) and never receives the
 * upstream Basic Auth credentials: they are attached here, on the server.
 * That property must survive the future Feishu login, so the front end can
 * stay unchanged when authentication lands.
 *
 * Transparency is the contract. Method, path, query, and body pass through
 * unchanged; the upstream status and safe response headers are returned as
 * received. The body is piped rather than buffered so `GET /event` streams
 * server-sent events with no added latency, and the client's abort signal is
 * forwarded so the upstream connection closes when the browser goes away.
 */

import { unavailable } from "../http/response";
import type { OpencodeResolution } from "./config";

export const PROXY_PREFIX = "/api/oc";

/**
 * Headers that describe the previous hop rather than the payload. Forwarding
 * them would either mislead the upstream or corrupt the response, since Bun
 * re-encodes the body.
 */
const HOP_BY_HOP = new Set([
  "connection",
  "keep-alive",
  "proxy-authenticate",
  "proxy-authorization",
  "te",
  "trailer",
  "transfer-encoding",
  "upgrade",
  "content-length",
  "host",
]);

/**
 * Never returned to the client. `authorization` and `www-authenticate` would
 * expose or invite use of the upstream credential scheme, and the browser
 * must not learn that the upstream uses Basic Auth at all.
 */
const STRIPPED_RESPONSE_HEADERS = new Set([
  "authorization",
  "www-authenticate",
  "proxy-authenticate",
]);

function requestHeaders(request: Request, authorization: string): Headers {
  const headers = new Headers();
  for (const [name, value] of request.headers) {
    if (HOP_BY_HOP.has(name.toLowerCase())) continue;
    // A client-supplied Authorization header must never override ours.
    if (name.toLowerCase() === "authorization") continue;
    headers.set(name, value);
  }
  headers.set("authorization", authorization);
  return headers;
}

function responseHeaders(upstream: Response): Headers {
  const headers = new Headers();
  for (const [name, value] of upstream.headers) {
    const lower = name.toLowerCase();
    if (HOP_BY_HOP.has(lower)) continue;
    if (STRIPPED_RESPONSE_HEADERS.has(lower)) continue;
    headers.set(name, value);
  }
  return headers;
}

/** Map the console path to the upstream path: /api/oc/session -> /session. */
export function upstreamPath(pathname: string): string {
  const rest = pathname.slice(PROXY_PREFIX.length);
  return rest === "" ? "/" : rest;
}

export function createOpencodeProxy(resolution: OpencodeResolution) {
  return async function proxy(request: Request, url: URL): Promise<Response> {
    if (!resolution.configured) {
      return unavailable(resolution.reason, resolution.detail);
    }

    const { baseUrl, username, password } = resolution.config;
    const target = new URL(upstreamPath(url.pathname), baseUrl);
    target.search = url.search;

    const authorization = `Basic ${btoa(`${username}:${password}`)}`;
    const init: RequestInit = {
      method: request.method,
      headers: requestHeaders(request, authorization),
      signal: request.signal,
      // Stream the request body instead of materializing it, so large
      // payloads and future streaming uploads both work.
      body:
        request.method === "GET" || request.method === "HEAD"
          ? undefined
          : request.body,
      redirect: "manual",
    };
    // Required by the Fetch spec when a stream is used as the body.
    if (init.body !== undefined) {
      (init as { duplex?: string }).duplex = "half";
    }

    let upstream: Response;
    try {
      upstream = await fetch(target, init);
    } catch (error) {
      // The client hung up: not an upstream failure, and no response is owed.
      if (request.signal.aborted) {
        return new Response(null, { status: 499 });
      }
      // Report unreachability without echoing the URL, which may embed
      // network topology the browser has no business learning.
      const detail =
        error instanceof Error && error.name === "TimeoutError"
          ? "the opencode server did not respond in time"
          : "the opencode server is not reachable";
      return unavailable("OPENCODE_UNREACHABLE", detail);
    }

    // Passing upstream.body through keeps SSE unbuffered: chunks reach the
    // client as opencode emits them.
    return new Response(upstream.body, {
      status: upstream.status,
      statusText: upstream.statusText,
      headers: responseHeaders(upstream),
    });
  };
}
