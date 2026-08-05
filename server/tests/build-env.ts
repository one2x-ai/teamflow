// Build/test environment is derived from the ambient environment so a proxy
// is only used when the developer actually has one. Never hardcode a proxy.
export function buildEnv(): Record<string, string> {
  return { ...(process.env as Record<string, string>) };
}
