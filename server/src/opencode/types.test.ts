import { test, expect, describe } from "bun:test";
import { isRenderedPart } from "./types";
import type { Part } from "./types";

const base = { id: "p1", sessionID: "s1", messageID: "m1" };

describe("isRenderedPart", () => {
  test("returns true for text parts", () => {
    const part: Part = { type: "text", ...base, text: "hello" };
    expect(isRenderedPart(part)).toBe(true);
  });

  test("returns true for reasoning parts", () => {
    const part: Part = { type: "reasoning", ...base, text: "thinking" };
    expect(isRenderedPart(part)).toBe(true);
  });

  test("returns true for tool parts", () => {
    const part: Part = { type: "tool", ...base, callID: "c1", tool: "bash", state: {} };
    expect(isRenderedPart(part)).toBe(true);
  });

  test("returns false for step-start parts", () => {
    const part: Part = { type: "step-start", ...base };
    expect(isRenderedPart(part)).toBe(false);
  });

  test("returns false for step-finish parts", () => {
    const part: Part = {
      type: "step-finish",
      ...base,
      reason: "done",
      cost: 0,
      tokens: { input: 0, output: 0, reasoning: 0, cache: { read: 0, write: 0 } },
    };
    expect(isRenderedPart(part)).toBe(false);
  });

  test("returns false for patch parts", () => {
    const part: Part = { type: "patch", ...base };
    expect(isRenderedPart(part)).toBe(false);
  });

  test("returns false for unknown future part types", () => {
    const part: Part = { type: "unknown-future-type", ...base };
    expect(isRenderedPart(part)).toBe(false);
  });
});
