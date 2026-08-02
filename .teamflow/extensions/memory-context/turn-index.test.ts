import { test, expect, describe } from "bun:test";
import {
	computeContentHash,
	serialize,
	deserialize,
	validateIndex,
	type TurnIndex,
	type SemanticEntry,
} from "./turn-index";

const sampleEntries: SemanticEntry[] = [
	{
		text: "Refactored authentication middleware to use OAuth2.",
		sources: [
			{ messageId: "msg-001", field: "text" },
			{ messageId: "msg-002", field: "toolCalls", toolCallId: "call-001" },
		],
	},
	{
		text: "Updated database connection pool settings.",
		sources: [{ messageId: "msg-004", field: "text" }],
	},
];

function makeIndex(overrides: Partial<TurnIndex> = {}): TurnIndex {
	const base: TurnIndex = {
		version: 1,
		blockId: "turn-abc123def456",
		repository: "teamflow/teamflow",
		taskId: "task-789",
		sessionId: "session-012",
		agent: "coder",
		sequence: 42,
		intent: "Refactor auth and database configuration",
		actions: [sampleEntries[0]],
		outcomes: [sampleEntries[1]],
		decisions: [
			{ text: "Adopted JWT for session tokens.", sources: [{ messageId: "msg-005", field: "text" }] },
		],
		constraints: [
			{ text: "Must support backward compatibility with v1 API.", sources: [{ messageId: "msg-001", field: "text" }] },
		],
		failures: [],
		openQuestions: [
			{ text: "Should we add rate limiting?", sources: [{ messageId: "msg-006", field: "text" }] },
		],
		keywords: ["oauth2", "jwt", "refactor", "database"],
		entities: ["AuthService", "DbPool", "SessionManager"],
		artifactRefs: ["memory://turn-abc123def456", "memory://rule-cache-42"],
		sourceEvents: [
			{ messageId: "msg-001", field: "text" },
			{ messageId: "msg-002", field: "toolCalls", toolCallId: "call-001" },
		],
		contentHash: "",
	};
	base.contentHash = computeContentHash(base);
	return { ...base, ...overrides };
}

describe("turn-index — canonical serialization", () => {
	test("serialize produces byte-identical output on repeated calls", () => {
		const index = makeIndex();
		expect(serialize(index)).toBe(serialize(index));
	});

	test("serialized body sections appear in the fixed canonical order", () => {
		const index = makeIndex();
		const xml = serialize(index);
		const order = [
			"actions",
			"outcomes",
			"decisions",
			"constraints",
			"failures",
			"open_questions",
			"keywords",
			"entities",
			"artifact_refs",
			"source_events",
		];
		const positions = order.map((tag) => xml.indexOf(`<${tag}>`));
		for (let i = 0; i < positions.length - 1; i++) {
			expect(positions[i]).toBeGreaterThan(-1);
			expect(positions[i]).toBeLessThan(positions[i + 1]);
		}
	});
});

describe("turn-index — content hash isolation", () => {
	test("hash excludes the contentHash field", () => {
		const indexA = makeIndex({ contentHash: "sha256:aaaa1111" });
		const indexB = makeIndex({ contentHash: "sha256:bbbb2222" });
		expect(computeContentHash(indexA)).toBe(computeContentHash(indexB));
	});
});

describe("turn-index — round-trip fidelity", () => {
	test("serialize → deserialize preserves all scalar fields", () => {
		const index = makeIndex();
		const restored = deserialize(serialize(index));
		expect(restored.version).toBe(index.version);
		expect(restored.blockId).toBe(index.blockId);
		expect(restored.repository).toBe(index.repository);
		expect(restored.taskId).toBe(index.taskId);
		expect(restored.sessionId).toBe(index.sessionId);
		expect(restored.agent).toBe(index.agent);
		expect(restored.sequence).toBe(index.sequence);
		expect(restored.intent).toBe(index.intent);
	});

	test("serialize → deserialize preserves SemanticEntry arrays", () => {
		const index = makeIndex();
		const restored = deserialize(serialize(index));
		expect(restored.actions.length).toBe(1);
		expect(restored.actions[0].text).toBe(index.actions[0].text);
		expect(restored.actions[0].sources.length).toBe(2);
		expect(restored.actions[0].sources[0].messageId).toBe("msg-001");
		expect(restored.actions[0].sources[0].field).toBe("text");
		expect(restored.actions[0].sources[1].toolCallId).toBe("call-001");
		expect(restored.decisions[0].text).toBe(index.decisions[0].text);
		expect(restored.openQuestions.length).toBe(1);
	});

	test("serialize → deserialize preserves IndexSourceRef objects with toolCallId", () => {
		const index = makeIndex();
		const restored = deserialize(serialize(index));
		expect(restored.sourceEvents.length).toBe(2);
		const ref = restored.sourceEvents[1];
		expect(ref.messageId).toBe("msg-002");
		expect(ref.field).toBe("toolCalls");
		expect(ref.toolCallId).toBe("call-001");
	});

	test("serialize → deserialize preserves flat list sections", () => {
		const index = makeIndex();
		const restored = deserialize(serialize(index));
		expect(restored.keywords).toEqual(index.keywords);
		expect(restored.entities).toEqual(index.entities);
		expect(restored.artifactRefs).toEqual(index.artifactRefs);
	});

	test("serialize → deserialize recomputes contentHash", () => {
		const index = makeIndex();
		const restored = deserialize(serialize(index));
		expect(restored.contentHash).toBe(index.contentHash);
	});
});

describe("turn-index — validateIndex", () => {
	test("accepts a well-formed index with correct content hash", () => {
		const index = makeIndex();
		expect(validateIndex(index)).toBe(true);
	});

	test("rejects an index whose contentHash does not match the recomputed hash", () => {
		const index = makeIndex();
		index.contentHash = "sha256:wronghash";
		expect(validateIndex(index)).toBe(false);
	});

	test("rejects wrong version", () => {
		const index = makeIndex({ version: 2 });
		expect(validateIndex(index)).toBe(false);
	});

	test("rejects non-object input", () => {
		expect(validateIndex(null)).toBe(false);
		expect(validateIndex("string")).toBe(false);
		expect(validateIndex(42)).toBe(false);
	});

	test("rejects empty blockId", () => {
		const index = makeIndex();
		index.blockId = "";
		expect(validateIndex(index)).toBe(false);
	});
});
