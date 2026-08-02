/**
 * CLI and environment resolution for `teamflow server`.
 *
 * Precedence throughout: CLI flag > environment variable > default. Nothing
 * here reaches the network or the filesystem beyond the `--dir` validation
 * that must fail startup, so the module stays trivially testable.
 */

export interface ServerConfig {
  host: string;
  port: number;
  /** Repository path passed via --dir, before git validation. */
  dir?: string;
  memory: MemoryConfig;
}

export interface MemoryConfig {
  root: string;
  configDir: string;
  home: string;
  project: string;
  semanticSearchEnabled: string;
}

export function failStartup(message: string): never {
  console.error(`teamflow server: ${message}`);
  process.exit(1);
}

interface ParsedArgs {
  host?: string;
  port?: string;
  dir?: string;
}

export function parseArgs(argv: string[]): ParsedArgs {
  const parsed: ParsedArgs = {};
  for (let index = 0; index < argv.length; index += 1) {
    const flag = argv[index];
    const value = argv[index + 1];
    if (flag === "--host" && value !== undefined) {
      parsed.host = value;
      index += 1;
    } else if (flag === "--port" && value !== undefined) {
      parsed.port = value;
      index += 1;
    } else if (flag === "--dir") {
      // A missing value is a usage error rather than a silent full-store view.
      if (value === undefined) failStartup("--dir requires a path value");
      parsed.dir = value;
      index += 1;
    }
  }
  return parsed;
}

export function resolveMemoryConfig(env: NodeJS.ProcessEnv): MemoryConfig {
  const root = env.TEAMFLOW_MEMORY_HOME ?? `${env.HOME}/.teamflow/memory`;
  return {
    root,
    configDir: `${root}/state`,
    home: `${root}/knowledge`,
    project: env.TEAMFLOW_MEMORY_PROJECT ?? "teamflow",
    // Keep retrieval offline and deterministic; vector search needs a local
    // FastEmbed model that may not be present.
    semanticSearchEnabled: "false",
  };
}

export function resolveServerConfig(
  argv: string[],
  env: NodeJS.ProcessEnv,
): ServerConfig {
  const args = parseArgs(argv);
  return {
    host: args.host ?? env.TEAMFLOW_SERVER_HOST ?? "127.0.0.1",
    port: Number(args.port ?? env.TEAMFLOW_SERVER_PORT ?? "7324"),
    dir: args.dir,
    memory: resolveMemoryConfig(env),
  };
}

export const PAGINATION: {
  /** Upstream page size when collecting candidates before local filtering. */
  fetchSize: number;
  maxPageSize: number;
  defaultPageSize: number;
} = {
  fetchSize: 100,
  maxPageSize: 100,
  defaultPageSize: 20,
};
