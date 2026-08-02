/**
 * Upstream opencode connection settings.
 *
 * opencode is the outer loop: it follows a persistent task session and
 * manages its own lifecycle. The console connects to a running instance and
 * never starts, stops, or supervises one.
 *
 * Credentials stay in this process. They are read here, attached by the
 * proxy, and never serialized into a client-facing response.
 */

export interface OpencodeConfig {
  baseUrl: string;
  username: string;
  password: string;
}

export type OpencodeResolution =
  | { configured: true; config: OpencodeConfig }
  | { configured: false; reason: string; detail: string };

interface OpencodeArgs {
  url?: string;
  username?: string;
  password?: string;
}

export function parseOpencodeArgs(argv: string[]): OpencodeArgs {
  const parsed: OpencodeArgs = {};
  for (let index = 0; index < argv.length; index += 1) {
    const flag = argv[index];
    const value = argv[index + 1];
    if (value === undefined) continue;
    if (flag === "--opencode-url") {
      parsed.url = value;
      index += 1;
    } else if (flag === "--opencode-user") {
      parsed.username = value;
      index += 1;
    } else if (flag === "--opencode-password") {
      parsed.password = value;
      index += 1;
    }
  }
  return parsed;
}

/**
 * Resolve the upstream, or explain precisely what is missing.
 *
 * A missing upstream is a degraded mode, not a startup failure: memory
 * browsing does not depend on opencode, so the server still starts and only
 * the proxy routes report unavailable.
 *
 * opencode generates random credentials when its own environment does not
 * set them, so there is nothing to guess: the operator must supply them.
 */
export function resolveOpencode(
  argv: string[],
  env: NodeJS.ProcessEnv,
): OpencodeResolution {
  const args = parseOpencodeArgs(argv);
  const rawUrl = args.url ?? env.TEAMFLOW_OPENCODE_URL;
  const username = args.username ?? env.TEAMFLOW_OPENCODE_USERNAME;
  const password = args.password ?? env.TEAMFLOW_OPENCODE_PASSWORD;

  if (rawUrl === undefined || rawUrl.trim() === "") {
    return {
      configured: false,
      reason: "OPENCODE_NOT_CONFIGURED",
      detail:
        "set --opencode-url or TEAMFLOW_OPENCODE_URL to the address of a running opencode server",
    };
  }

  let baseUrl: string;
  try {
    // Normalize away a trailing slash so path joining stays predictable.
    baseUrl = new URL(rawUrl).origin;
  } catch {
    return {
      configured: false,
      reason: "OPENCODE_URL_INVALID",
      detail: "the configured opencode URL is not a valid absolute URL",
    };
  }

  if (!username || !password) {
    return {
      configured: false,
      reason: "OPENCODE_CREDENTIALS_MISSING",
      detail:
        "opencode requires Basic Auth; set TEAMFLOW_OPENCODE_USERNAME and TEAMFLOW_OPENCODE_PASSWORD to match the values the opencode server was started with",
    };
  }

  return { configured: true, config: { baseUrl, username, password } };
}
