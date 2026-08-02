import { test, expect, describe } from "bun:test";
import {
	AUTHORITY_RANK,
	computeRuleContentHash,
	computeContentHash,
	serialize,
	deserialize,
	serializeRuleDelta,
	deserializeRuleDelta,
	validateDelta,
	type Rule,
	type RuleCache,
	type MemoryDelta,
	type DeltaOperation,
} from "./rule-cache";

function makeRule(overrides: Partial<Rule> = {}): Rule {
	return {
		id: "rule-001",
		key: "auth.method",
		kind: "constraint",
		authority: "repository",
		status: "active",
		scope: "project",
		source: "memory://turn-1#u1",
		content: "OAuth2 is the only allowed auth method.",
		contentHash: "",
		...overrides,
	};
}

function makeCache(overrides: Partial<RuleCache> = {}): RuleCache {
	return {
		version: 1,
		taskId: "task-42",
		rules: [makeRule()],
		contentHash: "",
		...overrides,
	};
}

describe("rule-cache — canonical serialization", () => {
	test("serialize produces byte-identical output on repeated calls", () => {
		const cache = makeCache();
		expect(serialize(cache)).toBe(serialize(cache));
	});
});

describe("rule-cache — content hash isolation", () => {
	test("cache hash excludes the root contentHash field", () => {
		const cacheA = makeCache({ contentHash: "sha256:aaaa" });
		const cacheB = makeCache({ contentHash: "sha256:bbbb" });
		expect(computeContentHash(cacheA)).toBe(computeContentHash(cacheB));
	});

	test("rule hash excludes the rule's own contentHash field", () => {
		const ruleA = makeRule({ contentHash: "sha256:aaa111" });
		const ruleB = makeRule({ contentHash: "sha256:bbb222" });
		expect(computeRuleContentHash(ruleA)).toBe(computeRuleContentHash(ruleB));
	});
});

describe("rule-cache — round-trip fidelity", () => {
	test("serialize → deserialize preserves all cache fields", () => {
		const cache = makeCache({
			rules: [
				makeRule(),
				makeRule({
					id: "rule-002",
					key: "db.driver",
					kind: "decision",
					authority: "user",
					status: "active",
					scope: "global",
					source: "memory://turn-5#a3",
					content: "Use PostgreSQL 16.",
				}),
			],
		});
		const restored = deserialize(serialize(cache));
		expect(restored.version).toBe(cache.version);
		expect(restored.taskId).toBe(cache.taskId);
		expect(restored.contentHash).toBe(computeContentHash(cache));
		expect(restored.rules.length).toBe(2);
	});

	test("serialize → deserialize preserves every field on each rule", () => {
		const original = makeRule({
			id: "rule-special",
			key: "deploy.strategy",
			kind: "non-goal",
			authority: "planner",
			status: "candidate",
			scope: "session",
			source: "memory://turn-9#u2",
			content: "Never deploy on Fridays.",
		});
		const cache = makeCache({ rules: [original] });
		const restored = deserialize(serialize(cache));
		const rule = restored.rules[0];
		expect(rule.id).toBe(original.id);
		expect(rule.key).toBe(original.key);
		expect(rule.kind).toBe(original.kind);
		expect(rule.authority).toBe(original.authority);
		expect(rule.status).toBe(original.status);
		expect(rule.scope).toBe(original.scope);
		expect(rule.source).toBe(original.source);
		expect(rule.content).toBe(original.content);
		expect(rule.contentHash).toBe(computeRuleContentHash(original));
	});
});

describe("rule-cache — delta round-trip", () => {
	test("serializeRuleDelta → deserializeRuleDelta preserves assert fields", () => {
		const op: DeltaOperation = {
			op: "assert",
			key: "lint.standard",
			kind: "constraint",
			authority: "user",
			scope: "project",
			source: "memory://turn-3#u1",
			content: "ESLint strict mode required.",
		};
		const delta: MemoryDelta = { operations: [op] };
		const restored = deserializeRuleDelta(serializeRuleDelta(delta))!;
		expect(restored.operations.length).toBe(1);
		const r = restored.operations[0];
		expect(r.op).toBe("assert");
		expect(r.key).toBe(op.key);
		expect(r.kind).toBe(op.kind);
		expect(r.authority).toBe(op.authority);
		expect(r.scope).toBe(op.scope);
		expect(r.source).toBe(op.source);
		expect(r.content).toBe(op.content);
	});

	test("serializeRuleDelta → deserializeRuleDelta preserves supersede fields", () => {
		const op: DeltaOperation = {
			op: "supersede",
			key: "api.version",
			kind: "decision",
			authority: "planner",
			scope: "global",
			source: "memory://turn-7#a1",
			content: "Target API v3 instead of v2.",
		};
		const delta: MemoryDelta = { operations: [op] };
		const restored = deserializeRuleDelta(serializeRuleDelta(delta))!;
		const r = restored.operations[0];
		expect(r.op).toBe("supersede");
		expect(r.key).toBe(op.key);
		expect(r.kind).toBe(op.kind);
		expect(r.authority).toBe(op.authority);
		expect(r.scope).toBe(op.scope);
		expect(r.source).toBe(op.source);
		expect(r.content).toBe(op.content);
	});

	test("serializeRuleDelta → deserializeRuleDelta preserves retire fields", () => {
		const op: DeltaOperation = {
			op: "retire",
			key: "legacy.endpoint",
			authority: "repository",
			source: "memory://turn-12#t1",
		};
		const delta: MemoryDelta = { operations: [op] };
		const restored = deserializeRuleDelta(serializeRuleDelta(delta))!;
		const r = restored.operations[0];
		expect(r.op).toBe("retire");
		expect(r.key).toBe(op.key);
		expect(r.authority).toBe(op.authority);
		expect(r.source).toBe(op.source);
	});

	test("deserializeRuleDelta returns null for input without <memory_delta>", () => {
		expect(deserializeRuleDelta('<rule_cache version="1"></rule_cache>')).toBeNull();
		expect(deserializeRuleDelta("just some text")).toBeNull();
	});
});

describe("rule-cache — validateDelta", () => {
	test("accepts a well-formed delta", () => {
		const delta: MemoryDelta = {
			operations: [
				{
					op: "assert",
					key: "valid.key",
					authority: "user",
					source: "memory://turn-1#u1",
					content: "Some constraint.",
				},
			],
		};
		expect(validateDelta(delta)).toBe(true);
	});

	test("rejects non-object input", () => {
		expect(validateDelta(null)).toBe(false);
		expect(validateDelta("not an object")).toBe(false);
		expect(validateDelta(42)).toBe(false);
	});

	test("rejects missing operations array", () => {
		expect(validateDelta({})).toBe(false);
		expect(validateDelta({ operations: "not-an-array" })).toBe(false);
	});

	test("rejects invalid op value", () => {
		const delta = { operations: [{ op: "invalid", key: "k", authority: "user", source: "s" }] };
		expect(validateDelta(delta)).toBe(false);
	});

	test("rejects empty key", () => {
		const delta = { operations: [{ op: "assert", key: "", authority: "user", source: "s" }] };
		expect(validateDelta(delta)).toBe(false);
	});

	test("rejects empty authority", () => {
		const delta = { operations: [{ op: "assert", key: "k", authority: "", source: "s" }] };
		expect(validateDelta(delta)).toBe(false);
	});

	test("accepts optional fields when present and string-typed", () => {
		const delta = {
			operations: [
				{
					op: "assert",
					key: "k",
					authority: "user",
					source: "s",
					kind: "constraint",
					scope: "project",
					content: "text",
				},
			],
		};
		expect(validateDelta(delta)).toBe(true);
	});

	test("AUTHORITY_RANK is exported with expected ranks", () => {
		expect(AUTHORITY_RANK.repository).toBe(5);
		expect(AUTHORITY_RANK.user).toBe(4);
		expect(AUTHORITY_RANK.planner).toBe(3);
		expect(AUTHORITY_RANK.tool_evidence).toBe(2);
		expect(AUTHORITY_RANK.candidate).toBe(1);
	});
});
