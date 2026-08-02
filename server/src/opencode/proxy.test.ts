import { test, expect, describe } from "bun:test";
import { upstreamPath } from "./proxy";

describe("upstreamPath", () => {
  test("strips /api/oc prefix for normal path", () => {
    expect(upstreamPath("/api/oc/session")).toBe("/session");
  });

  test("returns root when prefix is exact match", () => {
    expect(upstreamPath("/api/oc")).toBe("/");
  });

  test("returns root when prefix has trailing slash", () => {
    expect(upstreamPath("/api/oc/")).toBe("/");
  });

  test("handles deeply nested paths", () => {
    expect(upstreamPath("/api/oc/session/123/message")).toBe("/session/123/message");
  });

  test("handles event path", () => {
    expect(upstreamPath("/api/oc/event")).toBe("/event");
  });
});
