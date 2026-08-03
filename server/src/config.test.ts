import { test, expect, describe } from "bun:test";
import { resolveMemoryConfig } from "./config";

describe("resolveMemoryConfig", () => {
  test("TEAMFLOW_MEMORY_HOME takes highest precedence", () => {
    const cfg = resolveMemoryConfig({
      TEAMFLOW_MEMORY_HOME: "/explicit/memory",
      TEAMFLOW_HOME: "/should/be/ignored",
      HOME: "/should/be/ignored-too",
    });
    expect(cfg.root).toBe("/explicit/memory");
    expect(cfg.configDir).toBe("/explicit/memory/state");
    expect(cfg.home).toBe("/explicit/memory/knowledge");
  });

  test("falls back to TEAMFLOW_HOME/memory when TEAMFLOW_MEMORY_HOME is unset", () => {
    const cfg = resolveMemoryConfig({
      TEAMFLOW_HOME: "/workspace/.teamflow",
      HOME: "/workspace/opencode",
    });
    expect(cfg.root).toBe("/workspace/.teamflow/memory");
    expect(cfg.home).toBe("/workspace/.teamflow/memory/knowledge");
  });

  test("derives from HOME/.teamflow when neither memory var is set", () => {
    const cfg = resolveMemoryConfig({ HOME: "/workspace/opencode" });
    expect(cfg.root).toBe("/workspace/opencode/.teamflow/memory");
  });

  test("defaults project to teamflow, honors TEAMFLOW_MEMORY_PROJECT", () => {
    expect(resolveMemoryConfig({ HOME: "/h" }).project).toBe("teamflow");
    expect(
      resolveMemoryConfig({ HOME: "/h", TEAMFLOW_MEMORY_PROJECT: "mcap" }).project,
    ).toBe("mcap");
  });
});
