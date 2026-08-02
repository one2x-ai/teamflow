import { test, expect, describe } from "bun:test";
import {
	computeContentHash,
	serialize,
	deserialize,
	redactSecrets,
	type TurnBlock,
	type TurnMessage,
} from "./turn-block";

const messages: TurnMessage[] = [
	{
		id: "msg-001",
		role: "user",
		text: "Please refactor the auth module.",
	},
	{
		id: "msg-002",
		role: "assistant",
		text: "I will refactor the auth module now.",
		toolCalls: [
			{
				id: "call-001",
				name: "read_file",
				arguments: '{"path":"src/auth.ts"}',
			},
		],
	},
	{
		id: "msg-003",
		role: "toolResult",
		text: "file contents here",
		callId: "call-001",
		status: "ok",
	},
];

function makeBlock(overrides: Partial<TurnBlock> = {}): TurnBlock {
	return {
		version: 1,
		id: "turn-abc123def456",
		sequence: 42,
		previous: "sha256:prevhashdeadbeefcafe",
		repository: "teamflow/teamflow",
		taskId: "task-789",
		sessionId: "session-012",
		agent: "coder",
		startedAt: "2026-08-02T10:00:00Z",
		settledAt: "2026-08-02T10:05:30Z",
		contentHash: "",
		messages,
		...overrides,
	};
}

describe("turn-block — canonical serialization", () => {
	test("serialize produces byte-identical output on repeated calls", () => {
		const block = makeBlock();
		const first = serialize(block);
		const second = serialize(block);
		expect(first).toBe(second);
	});

	test("serialized attribute order is version, id, sequence, previous, repository, task_id, session_id, agent, started_at, settled_at, content_hash", () => {
		const block = makeBlock();
		const xml = serialize(block);
		const openTag = xml.match(/<teamflow_turn\s+([^>]*)>/s);
		expect(openTag).not.toBeNull();
		const attrs = openTag![1];
		const names = [...attrs.matchAll(/([a-z_]+)=/g)].map((m) => m[1]);
		expect(names).toEqual([
			"version",
			"id",
			"sequence",
			"previous",
			"repository",
			"task_id",
			"session_id",
			"agent",
			"started_at",
			"settled_at",
			"content_hash",
		]);
	});
});

describe("turn-block — content hash", () => {
	test("hash excludes the contentHash field", () => {
		const blockA = makeBlock({ contentHash: "sha256:aaaa1111" });
		const blockB = makeBlock({ contentHash: "sha256:bbbb2222" });
		expect(computeContentHash(blockA)).toBe(computeContentHash(blockB));
	});

	test("every hash starts with sha256:", () => {
		const block = makeBlock();
		const hash = computeContentHash(block);
		expect(hash.startsWith("sha256:")).toBe(true);
	});
});

describe("turn-block — round-trip fidelity", () => {
	test("serialize → deserialize preserves all scalar fields", () => {
		const block = makeBlock();
		const restored = deserialize(serialize(block));
		expect(restored.version).toBe(block.version);
		expect(restored.id).toBe(block.id);
		expect(restored.sequence).toBe(block.sequence);
		expect(restored.previous).toBe(block.previous);
		expect(restored.repository).toBe(block.repository);
		expect(restored.taskId).toBe(block.taskId);
		expect(restored.sessionId).toBe(block.sessionId);
		expect(restored.agent).toBe(block.agent);
		expect(restored.startedAt).toBe(block.startedAt);
		expect(restored.settledAt).toBe(block.settledAt);
		expect(restored.contentHash).toBe(computeContentHash(block));
	});

	test("serialize → deserialize preserves messages including toolCalls", () => {
		const block = makeBlock();
		const restored = deserialize(serialize(block));
		expect(restored.messages).toBeDefined();
		expect(restored.messages!.length).toBe(3);
		const assistant = restored.messages![1];
		expect(assistant.role).toBe("assistant");
		expect(assistant.toolCalls).toBeDefined();
		expect(assistant.toolCalls!.length).toBe(1);
		expect(assistant.toolCalls![0].id).toBe("call-001");
		expect(assistant.toolCalls![0].name).toBe("read_file");
		expect(assistant.toolCalls![0].arguments).toBe('{"path":"src/auth.ts"}');
	});

	test("serialize → deserialize preserves toolResult callId and status", () => {
		const block = makeBlock();
		const restored = deserialize(serialize(block));
		const toolResult = restored.messages![2];
		expect(toolResult.role).toBe("toolResult");
		expect(toolResult.callId).toBe("call-001");
		expect(toolResult.status).toBe("ok");
	});

	test("serialize → deserialize preserves user message text", () => {
		const block = makeBlock();
		const restored = deserialize(serialize(block));
		expect(restored.messages![0].text).toBe("Please refactor the auth module.");
	});
});

describe("turn-block — empty and missing messages", () => {
	test("messages: undefined serializes an empty <messages> body and round-trips", () => {
		const block = makeBlock({ messages: undefined });
		const xml = serialize(block);
		expect(xml).toContain("<messages>");
		expect(xml).toContain("</messages>");
		const restored = deserialize(xml);
		expect(restored.messages).toBeUndefined();
	});

	test("messages: [] serializes an empty <messages> body and round-trips", () => {
		const block = makeBlock({ messages: [] });
		const xml = serialize(block);
		expect(xml).toContain("<messages>");
		expect(xml).toContain("</messages>");
		expect(() => deserialize(xml)).not.toThrow();
	});
});

describe("turn-block — redactSecrets", () => {
	test("replaces sk- prefixed API keys (20+ alphanumeric)", () => {
		const text = "The key is sk-abcdefghijklmnopqrstuvwxyz1234567890";
		expect(redactSecrets(text)).toBe("The key is [REDACTED]");
	});

	test("replaces Bearer tokens", () => {
		const text = "Authorization: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9";
		expect(redactSecrets(text)).toBe("Authorization: [REDACTED]");
	});

	test("replaces private key blocks", () => {
		const text =
			"config:\n-----BEGIN RSA PRIVATE KEY-----\nMIIEpAIBAAKCAQEA...\n-----END RSA PRIVATE KEY-----\ndone";
		expect(redactSecrets(text)).toBe("config:\n[REDACTED]\ndone");
	});

	test("replaces key-value assignments (password=, api_key:, SECRET=, token=)", () => {
		expect(redactSecrets("password=hunter2")).toBe("[REDACTED]");
		expect(redactSecrets("api_key: abc123xyz")).toBe("[REDACTED]");
		expect(redactSecrets("SECRET=mysecret")).toBe("[REDACTED]");
		expect(redactSecrets("token=bearer123")).toBe("[REDACTED]");
	});

	test("preserves normal text unchanged", () => {
		const text = "The quick brown fox jumps over the lazy dog.";
		expect(redactSecrets(text)).toBe(text);
	});
});
