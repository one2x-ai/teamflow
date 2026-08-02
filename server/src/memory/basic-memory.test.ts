import { test, expect, describe } from "bun:test";
import { stripFrontmatter } from "./basic-memory";

describe("stripFrontmatter", () => {
  test("removes leading YAML frontmatter block", () => {
    expect(stripFrontmatter("---\ntitle: Test\n---\n\n# Body")).toBe("# Body");
  });

  test("returns content unchanged when no frontmatter present", () => {
    expect(stripFrontmatter("# Just a heading")).toBe("# Just a heading");
  });

  test("handles empty frontmatter block", () => {
    expect(stripFrontmatter("---\n---\n\nBody")).toBe("Body");
  });

  test("handles Windows-style CRLF line endings", () => {
    expect(stripFrontmatter("---\r\ntitle: T\r\n---\r\n\r\nBody")).toBe("Body");
  });

  test("handles complex YAML with nested arrays", () => {
    expect(stripFrontmatter("---\ntags:\n  - a\n  - b\n---\n\nText")).toBe("Text");
  });

  test("handles frontmatter with no trailing blank line", () => {
    expect(stripFrontmatter("---\nt: 1\n---\nBody")).toBe("Body");
  });
});
