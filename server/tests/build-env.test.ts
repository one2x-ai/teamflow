import { test, expect, describe, afterEach } from "bun:test";
import { buildEnv } from "./build-env";

const PROXY_KEYS = ["HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY"] as const;
const HARDCODED_LITERALS = ["127.0.0.1:1087", "socks5://127.0.0.1:1080"] as const;

describe("buildEnv", () => {
  const snapshot = new Map<string, string | undefined>();

  afterEach(() => {
    for (const key of PROXY_KEYS) {
      const original = snapshot.get(key);
      if (original === undefined) {
        delete process.env[key];
      } else {
        process.env[key] = original;
      }
    }
    snapshot.clear();
  });

  function captureProxyEnv(): void {
    for (const key of PROXY_KEYS) {
      snapshot.set(key, process.env[key]);
    }
  }

  test("omits hardcoded proxy literals when no proxy env vars are set", () => {
    captureProxyEnv();
    for (const key of PROXY_KEYS) {
      delete process.env[key];
    }

    const env = buildEnv();

    for (const key of PROXY_KEYS) {
      const value = env[key] as string | undefined;
      for (const literal of HARDCODED_LITERALS) {
        expect(value ?? "").not.toContain(literal);
      }
      expect(value).toBeUndefined();
    }
  });

  test("reflects ambient proxy env vars when set and omits them when absent", () => {
    captureProxyEnv();

    for (const key of PROXY_KEYS) {
      process.env[key] = `https://proxy.example.test/${key}`;
    }

    const envWith = buildEnv();
    for (const key of PROXY_KEYS) {
      expect((envWith[key] as string | undefined) ?? "").toBe(
        `https://proxy.example.test/${key}`,
      );
    }

    for (const key of PROXY_KEYS) {
      delete process.env[key];
    }

    const envWithout = buildEnv();
    for (const key of PROXY_KEYS) {
      expect(envWithout[key] as string | undefined).toBeUndefined();
    }
  });
});
