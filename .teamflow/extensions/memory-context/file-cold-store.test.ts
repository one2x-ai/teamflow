import { test, expect, describe, beforeEach, afterEach } from "bun:test";
import { FileColdStore, resolveRepositorySlug } from "./file-cold-store";
import { computeContentHash as computeTurnHash } from "./turn-block";
import { computeContentHash as computeIndexHash } from "./turn-index";
import type { TurnBlock } from "./turn-block";
import type { TurnIndex } from "./turn-index";
import { mkdtempSync, rmSync, existsSync, readFileSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

let tempDir: string;

beforeEach(() => {
	tempDir = mkdtempSync(join(tmpdir(), "teamflow-fcs-"));
});

afterEach(() => {
	rmSync(tempDir, { recursive: true, force: true });
});

function makeTurn(overrides: Partial<TurnBlock> = {}): TurnBlock {
	return {
		version: 1,
		id: "turn-abc123",
		sequence: 1,
		previous: null,
		repository: "test-repo",
		taskId: "task-001",
		sessionId: "session-001",
		agent: "coder",
		startedAt: "2026-08-02T10:00:00Z",
		settledAt: "2026-08-02T10:05:00Z",
		contentHash: "",
		messages: [{ id: "msg-1", role: "user", text: "Do the thing." }],
		...overrides,
	};
}

function makeIndex(overrides: Partial<TurnIndex> = {}): TurnIndex {
	const base: TurnIndex = {
		version: 1,
		blockId: "turn-abc123",
		repository: "test-repo",
		taskId: "task-001",
		sessionId: "session-001",
		agent: "coder",
		sequence: 1,
		intent: "",
		actions: [],
		outcomes: [],
		decisions: [],
		constraints: [],
		failures: [],
		openQuestions: [],
		keywords: [],
		entities: [],
		artifactRefs: [],
		sourceEvents: [],
		contentHash: "",
	};
	base.contentHash = computeIndexHash(base);
	return { ...base, ...overrides };
}

describe("file-cold-store — atomic write", () => {
	test("after writeTurn the .xml exists and no .tmp remains", async () => {
		const store = new FileColdStore(tempDir);
		const ref = await store.writeTurn(makeTurn());
		expect(existsSync(ref)).toBe(true);
		expect(existsSync(ref + ".tmp")).toBe(false);
	});
});

describe("file-cold-store — idempotent same-hash", () => {
	test("writing the same turn twice does not throw and returns the same path", async () => {
		const store = new FileColdStore(tempDir);
		const turn = makeTurn();
		const ref1 = await store.writeTurn(turn);
		const ref2 = await store.writeTurn(turn);
		expect(ref1).toBe(ref2);
	});
});

describe("file-cold-store — hash conflict", () => {
	test("same sequence and id with different content throws Hash conflict", async () => {
		const store = new FileColdStore(tempDir);
		const turnA = makeTurn({ messages: [{ id: "m1", role: "user", text: "Hello A" }] });
		const turnB = makeTurn({ messages: [{ id: "m1", role: "user", text: "Hello B" }] });
		await store.writeTurn(turnA);
		await expect(store.writeTurn(turnB)).rejects.toThrow("Hash conflict");
	});
});

describe("file-cold-store — readTurn integrity", () => {
	test("readTurn returns a turn whose recomputed hash matches", async () => {
		const store = new FileColdStore(tempDir);
		const ref = await store.writeTurn(makeTurn());
		const turn = await store.readTurn(ref);
		expect(turn.contentHash).toBe(computeTurnHash(turn));
	});

	test("tampering with file content causes readTurn to throw", async () => {
		const store = new FileColdStore(tempDir);
		const ref = await store.writeTurn(makeTurn());
		const original = readFileSync(ref, "utf-8");
		const tampered = original.replace("Do the thing.", "TAMPERED CONTENT");
		writeFileSync(ref, tampered, "utf-8");
		await expect(store.readTurn(ref)).rejects.toThrow();
	});
});

describe("file-cold-store — SAFE_SEGMENT validation", () => {
	test("repository '..' causes writeTurn to throw", async () => {
		const store = new FileColdStore(tempDir);
		await expect(store.writeTurn(makeTurn({ repository: ".." }))).rejects.toThrow();
	});
});

describe("file-cold-store — search determinism", () => {
	test("search returns results sorted by descending score", async () => {
		const store = new FileColdStore(tempDir);
		const indexHigh = makeIndex({
			blockId: "block-high",
			sequence: 2,
			sessionId: "session-a",
			keywords: ["oauth2"],
			intent: "Implement oauth2 authentication",
		});
		const indexLow = makeIndex({
			blockId: "block-low",
			sequence: 1,
			sessionId: "session-b",
			keywords: ["jwt"],
			intent: "Consider oauth2 for auth",
		});
		await store.writeIndex(indexHigh);
		await store.writeIndex(indexLow);
		const hits = await store.search("oauth2", { repository: "test-repo" });
		expect(hits.length).toBe(2);
		expect(hits[0].blockId).toBe("block-high");
		expect(hits[0].score).toBeGreaterThan(hits[1].score);
		expect(hits[1].blockId).toBe("block-low");
	});

	test("search breaks score ties by ascending sequence", async () => {
		const store = new FileColdStore(tempDir);
		const indexLate = makeIndex({
			blockId: "block-late",
			sequence: 5,
			sessionId: "session-a",
			intent: "Setup oauth2 flow",
		});
		const indexEarly = makeIndex({
			blockId: "block-early",
			sequence: 2,
			sessionId: "session-b",
			intent: "Migrate oauth2 tokens",
		});
		await store.writeIndex(indexLate);
		await store.writeIndex(indexEarly);
		const hits = await store.search("oauth2", { repository: "test-repo" });
		expect(hits.length).toBe(2);
		expect(hits[0].score).toBe(hits[1].score);
		expect(hits[0].blockId).toBe("block-early");
		expect(hits[1].blockId).toBe("block-late");
	});

	test("search deduplicates by blockId+taskId across sessions", async () => {
		const store = new FileColdStore(tempDir);
		const shared = {
			blockId: "block-shared",
			taskId: "task-shared",
			sequence: 1,
			keywords: ["oauth2"],
		};
		const index1 = makeIndex({ ...shared, sessionId: "session-001" });
		const index2 = makeIndex({ ...shared, sessionId: "session-002" });
		await store.writeIndex(index1);
		await store.writeIndex(index2);
		const hits = await store.search("oauth2", { repository: "test-repo" });
		expect(hits.length).toBe(1);
		expect(hits[0].blockId).toBe("block-shared");
	});
});

describe("file-cold-store — resolveRepositorySlug", () => {
	test("respects TEAMFLOW_REPOSITORY env override", () => {
		const old = process.env.TEAMFLOW_REPOSITORY;
		process.env.TEAMFLOW_REPOSITORY = "my-override-repo";
		try {
			expect(resolveRepositorySlug()).toBe("my-override-repo");
		} finally {
			if (old === undefined) delete process.env.TEAMFLOW_REPOSITORY;
			else process.env.TEAMFLOW_REPOSITORY = old;
		}
	});
});
