import { test, expect, describe } from "bun:test";
import { boundedInteger } from "./routes";

describe("boundedInteger", () => {
  test("returns the integer for valid positive input", () => {
    expect(boundedInteger("5")).toBe(5);
  });

  test("returns 1 for valid minimal positive input", () => {
    expect(boundedInteger("1")).toBe(1);
  });

  test("returns null for zero (must be positive)", () => {
    expect(boundedInteger("0")).toBeNull();
  });

  test("returns null for negative number", () => {
    expect(boundedInteger("-1")).toBeNull();
  });

  test("returns null for non-numeric string", () => {
    expect(boundedInteger("abc")).toBeNull();
  });

  test("returns null for empty string", () => {
    expect(boundedInteger("")).toBeNull();
  });

  test("returns null for non-integer decimal", () => {
    expect(boundedInteger("3.5")).toBeNull();
  });

  test("returns value within max bound", () => {
    expect(boundedInteger("5", 10)).toBe(5);
  });

  test("returns null when value exceeds max", () => {
    expect(boundedInteger("11", 10)).toBeNull();
  });

  test("returns value at max boundary (inclusive)", () => {
    expect(boundedInteger("10", 10)).toBe(10);
  });
});
