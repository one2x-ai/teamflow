/**
 * FileColdStore — file-based cold memory store.
 *
 * Writes immutable XML TurnBlocks to a separate cold-store directory
 * (<root>/<repository>/turns/<sessionId>/<seq>-<id>.xml), NOT to Basic
 * Memory's knowledge/ Markdown source tree. Because the files live in
 * their own state directory (by default
 * ~/.teamflow/memory/state/cold-store/), they never pollute the Basic
 * Memory knowledge tree and never trigger a pending dirty source.
 *
 * Renamed from basic-memory-adapter.ts for accuracy: this module writes
 * raw XML turn files to its own cold-store root and has no runtime
 * dependency on Basic Memory at all.
 *
 * Semantics: SAFE_SEGMENT path validation, atomic write (temp file +
 * rename), idempotent same-hash writes, hash-conflict errors, content
 * hash verification on read, and descending-sequence offset reads.
 */

import { execSync } from "node:child_process";
import * as fs from "node:fs";
import * as path from "node:path";
import type { TurnBlock } from "./turn-block";
import type { TurnIndex } from "./turn-index";
import type { ColdMemoryStore, MemoryRef, SessionScope } from "./cold-memory-store";
import type { MemoryScope, SearchHit, SearchOptions } from "./cold-memory-store";
import { serialize, deserialize, computeContentHash } from "./turn-block";
import { serialize as serializeIndex, deserialize as deserializeIndex, computeContentHash as computeIndexHash } from "./turn-index";

const SAFE_SEGMENT = /^[A-Za-z0-9][A-Za-z0-9._-]*$/;

function validateSegment(segment: string, name: string): void {
	if (segment === "..") {
		throw new Error(`Invalid ${name}: '..' is forbidden`);
	}
	if (!SAFE_SEGMENT.test(segment)) {
		throw new Error(`Invalid ${name}: '${segment}' does not match SAFE_SEGMENT`);
	}
}

// Run a git command in cwd, returning trimmed stdout or null on failure.
function gitCmd(cmd: string, cwd: string): string | null {
	try {
		return execSync(cmd, { cwd, encoding: "utf-8", stdio: ["pipe", "pipe", "ignore"] }).trim() || null;
	} catch { return null; }
}

// Slugify a remote URL or path into a safe repository segment.
function slugifyRepository(raw: string): string {
	const base = raw.replace(/\\/g, "/").split("/").pop() || raw;
	const stripped = base.replace(/\.git$/i, "");
	return stripped
		.toLowerCase()
		.replace(/[^a-z0-9._-]+/g, "-")
		.replace(/^-+/, "")
		.replace(/-+$/, "");
}

// Resolve the repository slug: TEAMFLOW_REPOSITORY env override first,
// then the git remote origin URL, then the git top-level directory name,
// then the current directory name. Never returns "default".
export function resolveRepositorySlug(cwd?: string): string {
	const envRepo = process.env.TEAMFLOW_REPOSITORY;
	if (envRepo && envRepo.trim()) return envRepo.trim();
	const dir = cwd || process.cwd();
	const remote = gitCmd("git config --get remote.origin.url", dir);
	if (remote) {
		const slug = slugifyRepository(remote);
		if (slug) return slug;
	}
	const topLevel = gitCmd("git rev-parse --show-toplevel", dir);
	if (topLevel) {
		const slug = slugifyRepository(topLevel);
		if (slug) return slug;
	}
	const slug = slugifyRepository(dir);
	return slug || "local";
}

export class FileColdStore implements ColdMemoryStore {
	constructor(private readonly rootPath: string) {}

	private turnDir(scope: SessionScope): string {
		// Validate all segments, then build the scope directory path.
		validateSegment(scope.repository, "repository");
		validateSegment(scope.taskId, "taskId");
		validateSegment(scope.sessionId, "sessionId");
		validateSegment(scope.agent, "agent");
		return path.join(this.rootPath, scope.repository, "turns", scope.sessionId);
	}

	private turnPath(scope: SessionScope, sequence: number, id: string): string {
		const dir = this.turnDir(scope);
		const seq = String(sequence).padStart(6, "0");
		return path.join(dir, `${seq}-${id}.xml`);
	}

	async writeTurn(turn: TurnBlock): Promise<MemoryRef> {
		const scope: SessionScope = {
			repository: turn.repository,
			taskId: turn.taskId,
			sessionId: turn.sessionId,
			agent: turn.agent,
		};
		const filePath = this.turnPath(scope, turn.sequence, turn.id);
		const body = serialize(turn);
		const hash = computeContentHash(turn);

		// Idempotent: if the file exists with the same hash, return without rewriting.
		if (fs.existsSync(filePath)) {
			const existing = fs.readFileSync(filePath, "utf-8");
			const existingHashMatch = existing.match(/content_hash="(sha256:[a-f0-9]+)"/);
			if (existingHashMatch && existingHashMatch[1] === hash) {
				return filePath;
			}
			throw new Error(`Hash conflict: ${filePath} already exists with different content hash`);
		}

		// Atomic write: temp file + rename.
		fs.mkdirSync(path.dirname(filePath), { recursive: true });
		const tmpPath = filePath + ".tmp";
		fs.writeFileSync(tmpPath, body, "utf-8");
		fs.renameSync(tmpPath, filePath);
		return filePath;
	}

	async writeIndex(index: TurnIndex): Promise<void> {
		// Index files live in a parallel turn-index/ tree; they are derived
		// artifacts and never replace the original TurnBlock under turns/.
		validateSegment(index.repository, "repository");
		validateSegment(index.sessionId, "sessionId");
		validateSegment(index.taskId, "taskId");
		const dir = path.join(this.rootPath, index.repository, "turn-index", index.sessionId);
		const seq = String(index.sequence).padStart(6, "0");
		// The taskId disambiguates indexes of the same block produced under
		// different tasks within one session.
		const filePath = path.join(dir, `${seq}-${index.blockId}-${index.taskId}.xml`);
		const body = serializeIndex(index);
		const hash = computeIndexHash(index);

		// Idempotent: if the file exists with the same hash, return without rewriting.
		if (fs.existsSync(filePath)) {
			const existing = fs.readFileSync(filePath, "utf-8");
			const existingHashMatch = existing.match(/content_hash="(sha256:[a-f0-9]+)"/);
			if (existingHashMatch && existingHashMatch[1] === hash) {
				return;
			}
			throw new Error(`Hash conflict: ${filePath} already exists with different content hash`);
		}

		// Atomic write: temp file + rename.
		fs.mkdirSync(dir, { recursive: true });
		const tmpPath = filePath + ".tmp";
		fs.writeFileSync(tmpPath, body, "utf-8");
		fs.renameSync(tmpPath, filePath);
	}

	async search(query: string, scope: MemoryScope, options?: SearchOptions): Promise<SearchHit[]> {
		validateSegment(scope.repository, "repository");
		const repoIndexDir = path.join(this.rootPath, scope.repository, "turn-index");
		if (!fs.existsSync(repoIndexDir)) return [];

		const tokens = query
			.split(/\s+/)
			.map((t) => t.toLowerCase())
			.filter((t) => t.length > 0);
		const limit = options?.limit ?? 10;

		interface ScoredIndex {
			index: TurnIndex;
			score: number;
			matchedFields: string[];
		}
		const candidates: ScoredIndex[] = [];

		// Walk session directories and files in sorted order for determinism.
		const sessionDirs = fs.readdirSync(repoIndexDir, { withFileTypes: true })
			.filter((d) => d.isDirectory())
			.map((d) => d.name)
			.sort();
		for (const sessionDirName of sessionDirs) {
			const dir = path.join(repoIndexDir, sessionDirName);
			const files = fs.readdirSync(dir).filter((f) => f.endsWith(".xml")).sort();
			for (const file of files) {
				const body = fs.readFileSync(path.join(dir, file), "utf-8");
				const index = deserializeIndex(body);

				// Scope filtering.
				if (scope.taskId !== undefined && index.taskId !== scope.taskId) continue;
				if (scope.sessionId !== undefined && index.sessionId !== scope.sessionId) continue;

				const fieldTexts: Record<string, string> = {
					intent: index.intent ?? "",
					keywords: (index.keywords ?? []).join(" "),
					entities: (index.entities ?? []).join(" "),
					actions: (index.actions ?? []).map((e) => e.text).join(" "),
					outcomes: (index.outcomes ?? []).map((e) => e.text).join(" "),
					decisions: (index.decisions ?? []).map((e) => e.text).join(" "),
					constraints: (index.constraints ?? []).map((e) => e.text).join(" "),
					failures: (index.failures ?? []).map((e) => e.text).join(" "),
					open_questions: (index.openQuestions ?? []).map((e) => e.text).join(" "),
				};

				let score = 0;
				const matchedFields: string[] = [];
				for (const [field, text] of Object.entries(fieldTexts)) {
					const lower = text.toLowerCase();
					let fieldMatched = false;
					for (const token of tokens) {
						if (lower.includes(token)) {
							score++;
							fieldMatched = true;
						}
					}
					if (fieldMatched) matchedFields.push(field);
				}
				if (score === 0) continue;
				matchedFields.sort();
				candidates.push({ index, score, matchedFields });
			}
		}

		// Deduplicate reindexed copies of the same block under the same
		// task: keep the highest score; tie-break by lowest sequence so
		// results stay deterministic.
		const byBlockId = new Map<string, ScoredIndex>();
		for (const candidate of candidates) {
			const dedupKey = `${candidate.index.blockId}${candidate.index.taskId}`;
			const existing = byBlockId.get(dedupKey);
			if (
				!existing ||
				candidate.score > existing.score ||
				(candidate.score === existing.score && candidate.index.sequence < existing.index.sequence)
			) {
				byBlockId.set(dedupKey, candidate);
			}
		}

		// Stable sort: descending score, then ascending sequence.
		const sorted = [...byBlockId.values()].sort(
			(a, b) => b.score - a.score || a.index.sequence - b.index.sequence,
		);

		return sorted.slice(0, limit).map((candidate) => {
			const seq = String(candidate.index.sequence).padStart(6, "0");
			// blockRef points at the original TurnBlock under turns/, never
			// at the derived turn-index/ file.
			const blockRef = path.join(
				this.rootPath,
				candidate.index.repository,
				"turns",
				candidate.index.sessionId,
				`${seq}-${candidate.index.blockId}.xml`,
			);
			return {
				blockRef,
				blockId: candidate.index.blockId,
				sequence: candidate.index.sequence,
				repository: candidate.index.repository,
				taskId: candidate.index.taskId,
				sessionId: candidate.index.sessionId,
				agent: candidate.index.agent,
				score: candidate.score,
				matchedFields: candidate.matchedFields,
			};
		});
	}

	async readTurn(ref: MemoryRef): Promise<TurnBlock> {
		const body = fs.readFileSync(ref, "utf-8");
		const turn = deserialize(body);
		// Verify integrity: recompute the content hash over the canonical form.
		const expectedHash = computeContentHash(turn);
		if (turn.contentHash !== expectedHash) {
			throw new Error(`Integrity failure: content hash mismatch in ${ref}`);
		}
		return turn;
	}

	async readByOffset(scope: SessionScope, before: number, count?: number): Promise<TurnBlock[]> {
		const dir = this.turnDir(scope);
		if (!fs.existsSync(dir)) return [];
		const files = fs.readdirSync(dir).filter((f) => f.endsWith(".xml"));
		// Parse sequence from the NNNNNN-id.xml filename prefix.
		const entries = files
			.map((f) => {
				const seq = parseInt(f.slice(0, 6), 10);
				return { file: f, sequence: seq };
			})
			.sort((a, b) => b.sequence - a.sequence); // descending by sequence

		const limit = count ?? 1;
		const startIdx = Math.min(before, entries.length);
		const result: TurnBlock[] = [];
		for (let i = startIdx; i < Math.min(startIdx + limit, entries.length); i++) {
			const filePath = path.join(dir, entries[i].file);
			const turn = await this.readTurn(filePath);
			result.push(turn);
		}
		return result;
	}
}
