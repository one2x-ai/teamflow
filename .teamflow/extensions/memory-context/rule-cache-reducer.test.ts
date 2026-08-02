import { test, expect, describe } from "bun:test";
import { authorityRank, applyDelta } from "./rule-cache-reducer";
import type { RuleCache, MemoryDelta, Rule } from "./rule-cache";

function makeRule(overrides: Partial<Rule> = {}): Rule {
	return {
		id: "rule-1",
		key: "test.key",
		kind: "constraint",
		authority: "repository",
		status: "active",
		scope: "project",
		source: "memory://turn-1#u1",
		content: "Base constraint.",
		contentHash: "",
		...overrides,
	};
}

function emptyCache(): RuleCache {
	return { version: 1, taskId: "task-1", rules: [], contentHash: "" };
}

function cacheWithRules(rules: Rule[]): RuleCache {
	return { version: 1, taskId: "task-1", rules, contentHash: "" };
}

describe("rule-cache-reducer — omission never deletes", () => {
	test("a delta that mentions no existing rules leaves the cache unchanged", () => {
		const cache = cacheWithRules([
			makeRule({ id: "rule-1", key: "alpha" }),
			makeRule({ id: "rule-2", key: "beta" }),
			makeRule({ id: "rule-3", key: "gamma" }),
		]);
		const delta: MemoryDelta = {
			operations: [
				{ op: "assert", key: "delta.key", authority: "user", source: "src", content: "new" },
			],
		};
		const result = applyDelta(cache, delta);
		const originalKeys = cache.rules.map((r) => r.key);
		const resultOriginals = result.rules.filter((r) => originalKeys.includes(r.key));
		expect(resultOriginals.length).toBe(3);
		for (const r of resultOriginals) {
			const orig = cache.rules.find((o) => o.key === r.key)!;
			expect(r.content).toBe(orig.content);
			expect(r.status).toBe(orig.status);
		}
	});
});

describe("rule-cache-reducer — authority ordering", () => {
	test("override refused: lower rank cannot overwrite higher", () => {
		let cache = emptyCache();
		const delta1: MemoryDelta = {
			operations: [{ op: "assert", key: "k", authority: "candidate", source: "s", content: "candidate text" }],
		};
		cache = applyDelta(cache, delta1);

		const delta2: MemoryDelta = {
			operations: [{ op: "assert", key: "k", authority: "planner", source: "s", content: "planner text" }],
		};
		cache = applyDelta(cache, delta2);
		expect(cache.rules[0].content).toBe("planner text");
		expect(cache.rules[0].authority).toBe("planner");

		const delta3: MemoryDelta = {
			operations: [{ op: "assert", key: "k", authority: "candidate", source: "s", content: "should not stick" }],
		};
		cache = applyDelta(cache, delta3);
		expect(cache.rules[0].content).toBe("planner text");
		expect(cache.rules[0].authority).toBe("planner");
	});

	test("supersede refused: lower rank cannot supersede higher", () => {
		const cache = cacheWithRules([makeRule({ key: "protected.key", authority: "repository" })]);
		const delta: MemoryDelta = {
			operations: [{ op: "supersede", key: "protected.key", authority: "user", source: "s", content: "x" }],
		};
		const result = applyDelta(cache, delta);
		const rule = result.rules.find((r) => r.key === "protected.key" && r.status === "active");
		expect(rule).toBeDefined();
		expect(rule!.status).toBe("active");
	});

	test("retire refused: lower rank cannot retire higher", () => {
		const cache = cacheWithRules([makeRule({ key: "protected.key", authority: "repository" })]);
		const delta: MemoryDelta = {
			operations: [{ op: "retire", key: "protected.key", authority: "tool_evidence", source: "s" }],
		};
		const result = applyDelta(cache, delta);
		const rule = result.rules.find((r) => r.key === "protected.key");
		expect(rule).toBeDefined();
		expect(rule!.status).toBe("active");
	});
});

describe("rule-cache-reducer — candidate status", () => {
	test("a rule asserted with candidate authority enters with status candidate", () => {
		const cache = emptyCache();
		const delta: MemoryDelta = {
			operations: [{ op: "assert", key: "k", authority: "candidate", source: "s", content: "tentative" }],
		};
		const result = applyDelta(cache, delta);
		expect(result.rules[0].status).toBe("candidate");
	});
});

describe("rule-cache-reducer — unknown authority", () => {
	test("authorityRank returns 1 for unknown authority", () => {
		expect(authorityRank("nonexistent")).toBe(1);
	});

	test("authorityRank returns correct values for known authorities", () => {
		expect(authorityRank("repository")).toBe(5);
		expect(authorityRank("user")).toBe(4);
		expect(authorityRank("planner")).toBe(3);
		expect(authorityRank("tool_evidence")).toBe(2);
		expect(authorityRank("candidate")).toBe(1);
	});
});

describe("rule-cache-reducer — supersede preserves old rule", () => {
	test("supersede marks old rule superseded and adds a new one with the same key", () => {
		const cache = cacheWithRules([
			makeRule({ id: "rule-1", key: "shared.key", authority: "repository", content: "old content" }),
		]);
		const delta: MemoryDelta = {
			operations: [
				{
					op: "supersede",
					key: "shared.key",
					authority: "repository",
					source: "memory://turn-5#u2",
					content: "new content",
				},
			],
		};
		const result = applyDelta(cache, delta);
		expect(result.rules.length).toBe(2);
		const superseded = result.rules.find((r) => r.status === "superseded");
		expect(superseded).toBeDefined();
		expect(superseded!.key).toBe("shared.key");
		expect(superseded!.content).toBe("old content");
		const fresh = result.rules.find((r) => r.status === "active");
		expect(fresh).toBeDefined();
		expect(fresh!.key).toBe("shared.key");
		expect(fresh!.content).toBe("new content");
	});
});

describe("rule-cache-reducer — retire changes status only", () => {
	test("retire sets the existing rule status to retired", () => {
		const cache = cacheWithRules([
			makeRule({ id: "rule-1", key: "retire.key", authority: "repository" }),
		]);
		const delta: MemoryDelta = {
			operations: [{ op: "retire", key: "retire.key", authority: "repository", source: "memory://turn-9#t1" }],
		};
		const result = applyDelta(cache, delta);
		expect(result.rules.length).toBe(1);
		expect(result.rules[0].status).toBe("retired");
	});
});

describe("rule-cache-reducer — tool_evidence empty source rejected", () => {
	test("a tool_evidence assert with empty source adds no rule", () => {
		const cache = emptyCache();
		const delta: MemoryDelta = {
			operations: [{ op: "assert", key: "k", authority: "tool_evidence", source: "", content: "evidence" }],
		};
		const result = applyDelta(cache, delta);
		expect(result.rules.length).toBe(0);
	});

	test("a tool_evidence assert with non-empty source succeeds", () => {
		const cache = emptyCache();
		const delta: MemoryDelta = {
			operations: [
				{ op: "assert", key: "k", authority: "tool_evidence", source: "memory://turn-3#t1", content: "evidence" },
			],
		};
		const result = applyDelta(cache, delta);
		expect(result.rules.length).toBe(1);
		expect(result.rules[0].status).toBe("active");
	});
});

describe("rule-cache-reducer — purity", () => {
	test("the input cache rules array is not mutated", () => {
		const originalRule = makeRule({ id: "rule-1", key: "immutable.key", content: "original" });
		const cache = cacheWithRules([originalRule]);
		const delta: MemoryDelta = {
			operations: [{ op: "assert", key: "immutable.key", authority: "user", source: "s", content: "changed" }],
		};
		applyDelta(cache, delta);
		expect(cache.rules[0].content).toBe("original");
		expect(cache.rules[0].status).toBe("active");
	});
});

describe("rule-cache-reducer — deterministic IDs", () => {
	test("applying the same delta to the same empty cache twice produces identical rule IDs", () => {
		const delta: MemoryDelta = {
			operations: [
				{ op: "assert", key: "a", authority: "user", source: "s1", content: "alpha" },
				{ op: "assert", key: "b", authority: "user", source: "s2", content: "beta" },
			],
		};
		const result1 = applyDelta(emptyCache(), delta);
		const result2 = applyDelta(emptyCache(), delta);
		expect(result1.rules.map((r) => r.id)).toEqual(result2.rules.map((r) => r.id));
		expect(result1.rules[0].id).toBe("rule-1");
		expect(result1.rules[1].id).toBe("rule-2");
	});
});
