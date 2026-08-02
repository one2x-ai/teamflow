import { test, expect, describe } from "bun:test";
import { resolveOpencode } from "./config";

describe("resolveOpencode", () => {
  test("returns OPENCODE_NOT_CONFIGURED when no URL in env or args", () => {
    const result = resolveOpencode([], {});
    expect(result.configured).toBe(false);
    if (!result.configured) {
      expect(result.reason).toBe("OPENCODE_NOT_CONFIGURED");
    }
  });

  test("returns configured:true when URL provided via env", () => {
    const result = resolveOpencode([], {
      TEAMFLOW_OPENCODE_URL: "http://localhost:7396",
      TEAMFLOW_OPENCODE_USERNAME: "u",
      TEAMFLOW_OPENCODE_PASSWORD: "p",
    });
    expect(result.configured).toBe(true);
    if (result.configured) {
      expect(result.config.baseUrl).toBe("http://localhost:7396");
      expect(result.config.username).toBe("u");
      expect(result.config.password).toBe("p");
    }
  });

  test("returns configured:true when URL provided via args", () => {
    const result = resolveOpencode(
      ["--opencode-url", "http://x:1", "--opencode-user", "u", "--opencode-password", "p"],
      {},
    );
    expect(result.configured).toBe(true);
    if (result.configured) {
      expect(result.config.baseUrl).toBe("http://x:1");
    }
  });

  test("returns OPENCODE_URL_INVALID for malformed URL", () => {
    const result = resolveOpencode([], {
      TEAMFLOW_OPENCODE_URL: "not-a-url",
      TEAMFLOW_OPENCODE_USERNAME: "u",
      TEAMFLOW_OPENCODE_PASSWORD: "p",
    });
    expect(result.configured).toBe(false);
    if (!result.configured) {
      expect(result.reason).toBe("OPENCODE_URL_INVALID");
    }
  });

  test("returns OPENCODE_CREDENTIALS_MISSING when username or password absent", () => {
    const result = resolveOpencode([], { TEAMFLOW_OPENCODE_URL: "http://x:1" });
    expect(result.configured).toBe(false);
    if (!result.configured) {
      expect(result.reason).toBe("OPENCODE_CREDENTIALS_MISSING");
    }
  });

  test("normalizes trailing slash from baseUrl", () => {
    const result = resolveOpencode([], {
      TEAMFLOW_OPENCODE_URL: "http://localhost:7396/",
      TEAMFLOW_OPENCODE_USERNAME: "u",
      TEAMFLOW_OPENCODE_PASSWORD: "p",
    });
    expect(result.configured).toBe(true);
    if (result.configured) {
      expect(result.config.baseUrl).toBe("http://localhost:7396");
    }
  });

  test("arg URL overrides env URL", () => {
    const result = resolveOpencode(
      ["--opencode-url", "http://arg-host:1", "--opencode-user", "au", "--opencode-password", "ap"],
      { TEAMFLOW_OPENCODE_URL: "http://env-host:2", TEAMFLOW_OPENCODE_USERNAME: "eu", TEAMFLOW_OPENCODE_PASSWORD: "ep" },
    );
    expect(result.configured).toBe(true);
    if (result.configured) {
      expect(result.config.baseUrl).toBe("http://arg-host:1");
      expect(result.config.username).toBe("au");
      expect(result.config.password).toBe("ap");
    }
  });
});
