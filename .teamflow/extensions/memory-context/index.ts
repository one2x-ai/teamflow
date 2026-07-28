/**
 * Teamflow memory-context extension — Phase D: observation, cold memory
 * persistence, visible XML context injection, hot-zone context
 * projection, and no-compact interception.
 *
 * Observation (Phase A, unchanged): registers before_agent_start /
 * tool_call / tool_result / agent_settled / session_start hooks to track
 * turn boundaries and tool causal pairs, computes SHA-256 observation
 * manifests (systemPromptHash, contextMessagesHash, manifestHash), and
 * appends exactly one immutable observation receipt per turn via
 * appendEntry("teamflow:observation", ...).
 *
 * Persistence (Phase B): at agent_settled the extension also builds a
 * complete TurnBlock (metadata + redacted user/assistant/toolResult
 * message content) from the session entries of the current turn and
 * persists it through the replaceable ColdMemoryStore interface
 * (FileColdStore by default, root overridable via
 * TEAMFLOW_COLD_MEMORY_ROOT, defaulting to the state/cold-store
 * directory under ~/.teamflow/memory). The outcome is reported as one
 * appendEntry("teamflow:cold_memory_persistence", ...) receipt per turn:
 * status "persisted" with the store ref on success, or status "failed"
 * with reason "MEMORY_PERSISTENCE_FAILED" on error — failures are never
 * faked as success.
 *
 * Visible XML (Phase C): pi-runtime passes --no-context-files so Pi does
 * NOT auto-load AGENTS.md into the system prompt. Instead, before_agent_start
 * reads AGENTS.md explicitly and returns a display:true custom message
 * containing <teamflow_context> XML with a <context_manifest> listing the
 * source kind and SHA-256 hash. No hidden systemPrompt concatenation occurs.
 *
 * Hot-zone projection (Phase D): the context hook receives a deep copy
 * of the session messages about to be sent to the LLM and returns a
 * replacement projection. The projection keeps the latest
 * teamflow:context message (project rules), the latest completed turn,
 * and the active turn. Older turns are evicted from the projection but
 * remain intact in the FileColdStore for exact recall — no replacement
 * text is generated for evicted content. Tool call / tool result causal
 * pairs are never split across the eviction boundary. When the
 * protected context exceeds the model budget, a structured
 * CONTEXT_BUDGET_EXCEEDED receipt is appended instead of retrying with
 * compaction.
 *
 * No-compact (Phase D): session_before_compact returns { cancel: true }
 * for every reason (manual, threshold, overflow) and appends a
 * teamflow:compact_intercepted receipt. session_compact appends a
 * teamflow:compact_violation receipt, because compaction after
 * cancellation is a runtime invariant violation.
 *
 * Rule cache (Phase E): a protected, long-lived constraint layer
 * (准则 cache). before_agent_start projects the current rule cache
 * (active/candidate rules only) as a visible <rule_cache> XML section
 * inside teamflow_context, with a kind="rule_cache" source in the
 * context_manifest. agent_settled extracts the agent's teamflow_result,
 * and only when the result is not truncated (finish=length), its status
 * is PASS, and the embedded <memory_delta> passes structural validation
 * does it apply the delta through the authority-aware reducer and
 * persist the new cache as a teamflow:rule_cache custom entry.
 * session_start restores the latest persisted rule cache after
 * verifying its canonical content hash. The rule cache is persisted
 * only as in-session custom entries, never as Basic Memory notes.
 *
 * Invariants: it does NOT inject or replace system prompt content
 * (before_agent_start returns a visible message, never a systemPrompt
 * field), and never lets compaction run.
 */

import { createHash } from "node:crypto";
import * as fs from "node:fs";
import * as path from "node:path";
import * as os from "node:os";
import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";
import { computeContentHash, redactSecrets } from "./turn-block";
import type { TurnBlock, TurnMessage } from "./turn-block";
import type { ColdMemoryStore } from "./cold-memory-store";
import { FileColdStore, resolveRepositorySlug } from "./file-cold-store";
import type { RuleCache, MemoryDelta } from "./rule-cache";
import {
	serialize as serializeRuleCache,
	deserialize as deserializeRuleCache,
	computeContentHash as computeRuleCacheHash,
	validateDelta,
	deserializeRuleDelta,
} from "./rule-cache";
import { applyDelta } from "./rule-cache-reducer";

const OBSERVATION_CUSTOM_TYPE = "teamflow:observation";
const OBSERVATION_VERSION = 1;
const COLD_MEMORY_PERSISTENCE_TYPE = "teamflow:cold_memory_persistence";
const CONTEXT_CUSTOM_TYPE = "teamflow:context";
const COMPACT_INTERCEPTED_TYPE = "teamflow:compact_intercepted";
const COMPACT_VIOLATION_TYPE = "teamflow:compact_violation";
const BUDGET_EXCEEDED_TYPE = "teamflow:context_budget_exceeded";
const CONTEXT_BUDGET_EXCEEDED = "CONTEXT_BUDGET_EXCEEDED";
const RECALL_BUDGET_EXCEEDED_TYPE = "teamflow:recall_budget_exceeded";
const RECALL_BUDGET_EXCEEDED = "RECALL_BUDGET_EXCEEDED";
const RULE_CACHE_CUSTOM_TYPE = "teamflow:rule_cache";

interface ObservationReceipt {
	version: number;
	turnIndex: number;
	startedAt: string;
	settledAt: string;
	systemPromptHash: string;
	contextMessagesHash: string;
	manifestHash: string;
	toolCalls: number;
	toolResults: number;
	unmatchedCalls: string[];
	unmatchedResults: string[];
	budget: {
		tokens: number | null;
		contextWindow: number;
		percent: number | null;
	} | null;
}

function sha256Hex(input: string): string {
	return createHash("sha256").update(input).digest("hex");
}

// Escape dynamic text for embedding inside the XML context message.
function escapeXmlContent(value: string): string {
	return value
		.replace(/&/g, "&amp;")
		.replace(/</g, "&lt;")
		.replace(/>/g, "&gt;");
}

function isRecord(value: unknown): value is Record<string, unknown> {
	return typeof value === "object" && value !== null;
}

function isRestorableReceipt(value: unknown): value is Pick<ObservationReceipt, "version" | "turnIndex"> {
	if (!isRecord(value)) return false;
	return (
		value.version === OBSERVATION_VERSION &&
		typeof value.turnIndex === "number" &&
		Number.isInteger(value.turnIndex) &&
		value.turnIndex >= 0
	);
}

// Create the default ColdMemoryStore. The root is overridable via
// TEAMFLOW_COLD_MEMORY_ROOT so tests and operators can isolate storage.
function createMemoryStore(): ColdMemoryStore {
	const rootPath = process.env.TEAMFLOW_COLD_MEMORY_ROOT
		|| path.join(os.homedir(), ".teamflow", "memory", "state", "cold-store");
	return new FileColdStore(rootPath);
}

// Extract the text of a message content field, which may be a plain
// string or an array of typed content parts. All text is passed through
// redactSecrets() before it reaches the persisted TurnBlock.
function extractText(content: unknown): string {
	if (typeof content === "string") return redactSecrets(content);
	if (!Array.isArray(content)) return "";
	const parts: string[] = [];
	for (const item of content) {
		if (isRecord(item) && item.type === "text" && typeof item.text === "string") {
			parts.push(redactSecrets(item.text));
		}
	}
	return parts.join("\n");
}

// Check whether an assistant message carries a tool call with the given
// id inside its content array. Used by the hot-zone projection to keep a
// tool call / tool result causal pair together across the cut boundary.
function hasToolCallId(message: Record<string, unknown>, callId: unknown): boolean {
	if (callId === undefined || callId === null) return false;
	const content = message.content;
	if (!Array.isArray(content)) return false;
	for (const item of content) {
		if (!isRecord(item)) continue;
		if (item.type === "toolCall" && item.id === callId) return true;
	}
	return false;
}

// Extract tool calls from an assistant message content array. Tool call
// parts carry id / name / arguments; arguments are serialized canonically
// via JSON.stringify.
function extractToolCalls(content: unknown): { id: string; name: string; arguments: string }[] {
	if (!Array.isArray(content)) return [];
	const calls: { id: string; name: string; arguments: string }[] = [];
	for (const item of content) {
		if (!isRecord(item)) continue;
		if (item.type !== "toolCall") continue;
		calls.push({
			id: String(item.id ?? ""),
			name: String(item.name ?? ""),
			arguments: JSON.stringify(item.arguments ?? {}),
		});
	}
	return calls;
}

// Build TurnMessage[] from session entries. Entries with type "message"
// are converted by role; secrets are redacted from all text before
// storage.
function extractMessages(entries: unknown[]): TurnMessage[] {
	const messages: TurnMessage[] = [];
	for (const entry of entries) {
		if (!isRecord(entry)) continue;
		if (entry.type !== "message") continue;
		const message = entry.message;
		if (!isRecord(message)) continue;
		const role = message.role;
		if (role === "user") {
			messages.push({
				id: `user-${messages.length + 1}`,
				role: "user",
				text: extractText(message.content),
			});
		} else if (role === "assistant") {
			const msg: TurnMessage = {
				id: `assistant-${messages.length + 1}`,
				role: "assistant",
				text: extractText(message.content),
			};
			const toolCalls = extractToolCalls(message.content);
			if (toolCalls.length > 0) msg.toolCalls = toolCalls;
			messages.push(msg);
		} else if (role === "toolResult") {
			messages.push({
				id: `toolResult-${messages.length + 1}`,
				role: "toolResult",
				text: extractText(message.content),
				callId: typeof message.toolCallId === "string" ? message.toolCallId : "",
				status: message.isError === true ? "error" : "ok",
			});
		}
	}
	return messages;
}

export default function (pi: ExtensionAPI) {
	// Per-session state, sealed one turn at a time.
	let turnIndex = 0;
	let currentSystemPrompt = "";
	let currentCwd = "";
	let startedAt = "";
	let toolCallIds = new Set<string>();
	let toolResultIds = new Set<string>();
	const store: ColdMemoryStore = createMemoryStore();
	// Phase E: per-session rule cache (准则 cache). Restored from the
	// latest teamflow:rule_cache session entry on session_start.
	let currentRuleCache: RuleCache = { version: 1, taskId: "adhoc", rules: [], contentHash: "" };

	pi.on("session_start", (_event, ctx) => {
		// Restore the turn counter from the latest prior observation receipt
		// in this session, so reloads continue the sequence instead of
		// restarting at zero. Malformed or version-mismatched entries are
		// ignored and yield a fresh start.
		let restored = 0;
		for (const entry of ctx.sessionManager.getEntries()) {
			if (entry.type !== "custom") continue;
			const custom = entry as { customType?: unknown; data?: unknown };
			if (custom.customType !== OBSERVATION_CUSTOM_TYPE) continue;
			if (isRestorableReceipt(custom.data)) {
				restored = Math.max(restored, custom.data.turnIndex);
			}
		}
		turnIndex = restored;

		// Phase E: restore the rule cache from the latest
		// teamflow:rule_cache custom entry. The canonical content hash is
		// verified before the entry is trusted; a hash mismatch or a
		// malformed entry is ignored and leaves the cache unchanged.
		for (const entry of ctx.sessionManager.getEntries()) {
			if (entry.type !== "custom") continue;
			const custom = entry as { customType?: unknown; data?: unknown };
			if (custom.customType !== RULE_CACHE_CUSTOM_TYPE) continue;
			if (!isRecord(custom.data) || typeof custom.data.xml !== "string") continue;
			try {
				const restoredCache = deserializeRuleCache(custom.data.xml);
				// Verify the canonical hash on restore.
				if (
					restoredCache.contentHash !== "" &&
					computeRuleCacheHash(restoredCache) === restoredCache.contentHash
				) {
					currentRuleCache = restoredCache;
				}
			} catch {
				// Malformed rule cache entry — keep the current cache.
			}
		}
	});

	pi.on("before_agent_start", (event) => {
		turnIndex += 1;
		currentSystemPrompt = event.systemPrompt;
		startedAt = new Date().toISOString();
		toolCallIds = new Set<string>();
		toolResultIds = new Set<string>();

		// Phase C: inject a visible XML context message carrying the
		// project rules. Pi is launched with --no-context-files, so
		// AGENTS.md is read explicitly here instead of being hidden in
		// the system prompt. A missing AGENTS.md yields an empty
		// project_rules section rather than an error.
		const cwd = event.systemPromptOptions?.cwd || process.cwd();
		currentCwd = cwd;
		let rulesContent = "";
		try {
			rulesContent = fs.readFileSync(path.join(cwd, "AGENTS.md"), "utf-8");
		} catch {
			// AGENTS.md not found — project_rules section will be empty.
		}
		const rulesHash = "sha256:" + createHash("sha256").update(rulesContent).digest("hex");
		const generatedAt = new Date().toISOString();

		// Phase E: project the rule cache into the visible context. Only
		// active and candidate rules are shown; superseded/retired rules
		// stay in the cache for audit. The cache gets its own
		// kind="rule_cache" source entry in the context_manifest.
		const visibleCache: RuleCache = {
			version: currentRuleCache.version,
			taskId: currentRuleCache.taskId,
			rules: currentRuleCache.rules.filter(
				(rule) => rule.status === "active" || rule.status === "candidate",
			),
			contentHash: "",
		};
		visibleCache.contentHash = computeRuleCacheHash(visibleCache);
		const ruleCacheRef = `cache://${visibleCache.taskId}`;
		const ruleCacheXml = serializeRuleCache(visibleCache).replace(/\n/g, "\n  ");

		const xml = `<teamflow_context version="1">
  <context_manifest generated_at="${generatedAt}">
    <source kind="project_rules" ref="AGENTS.md" hash="${rulesHash}" />
    <source kind="rule_cache" ref="${ruleCacheRef}" hash="${visibleCache.contentHash}" />
  </context_manifest>
  <project_rules>
${escapeXmlContent(rulesContent)}
  </project_rules>
  ${ruleCacheXml}
</teamflow_context>`;

		return {
			message: {
				customType: CONTEXT_CUSTOM_TYPE,
				content: xml,
				display: true,
				details: {
					sources: [
						{ kind: "project_rules", ref: "AGENTS.md", hash: rulesHash },
						{ kind: "rule_cache", ref: ruleCacheRef, hash: visibleCache.contentHash },
					],
					generatedAt,
				},
			},
		};
	});

	pi.on("tool_call", (event) => {
		toolCallIds.add(event.toolCallId);
	});

	pi.on("tool_result", (event) => {
		toolResultIds.add(event.toolCallId);
	});

	pi.on("agent_settled", async (_event, ctx) => {
		// Seal the current turn: hash the observed surface and validate the
		// causal pairing between tool calls and their results.
		const settledAt = new Date().toISOString();
		// Observe the budget / context-usage surface at seal time. This is
		// observation-only: the values are recorded in the receipt, never
		// used for control. getContextUsage may return undefined, in which
		// case the budget field is null.
		const contextUsage = ctx.getContextUsage();
		const budget = contextUsage
			? {
				tokens: contextUsage.tokens,
				contextWindow: contextUsage.contextWindow,
				percent: contextUsage.percent,
			}
			: null;
		const systemPromptHash = sha256Hex(currentSystemPrompt);
		const sortedCallIds = [...toolCallIds].sort();
		const sortedResultIds = [...toolResultIds].sort();
		const contextMessagesHash = sha256Hex(
			JSON.stringify({ calls: sortedCallIds, results: sortedResultIds }),
		);
		const manifestHash = sha256Hex(`${systemPromptHash}:${contextMessagesHash}`);
		const unmatchedCalls = sortedCallIds.filter((id) => !toolResultIds.has(id));
		const unmatchedResults = sortedResultIds.filter((id) => !toolCallIds.has(id));

		const receipt: ObservationReceipt = {
			version: OBSERVATION_VERSION,
			turnIndex,
			startedAt,
			settledAt,
			systemPromptHash,
			contextMessagesHash,
			manifestHash,
			toolCalls: toolCallIds.size,
			toolResults: toolResultIds.size,
			unmatchedCalls,
			unmatchedResults,
			budget,
		};

		// Phase B: build and persist a complete TurnBlock for this turn.
		// The turn boundary is the last observation receipt entry; all
		// entries after it belong to the current turn.
		const turnId = `turn-${turnIndex}`;
		try {
			const entries = ctx.sessionManager.getEntries();
			let boundary = -1;
			for (let i = entries.length - 1; i >= 0; i--) {
				const entry = entries[i] as { type?: unknown; customType?: unknown };
				if (entry.type === "custom" && entry.customType === OBSERVATION_CUSTOM_TYPE) {
					boundary = i;
					break;
				}
			}
			const turnEntries = entries.slice(boundary + 1);
			const messages = extractMessages(turnEntries);

			const turnBlock: TurnBlock = {
				version: 1,
				id: turnId,
				sequence: turnIndex,
				previous: turnIndex > 1 ? `turn-${turnIndex - 1}` : null,
				repository: resolveRepositorySlug(currentCwd),
				taskId: process.env.TEAMFLOW_TASK_ID || "adhoc",
				sessionId: ctx.sessionManager.getSessionId(),
				agent: process.env.TEAMFLOW_AGENT_ROLE || "unknown",
				startedAt,
				settledAt,
				contentHash: "",
				messages,
			};
			turnBlock.contentHash = computeContentHash(turnBlock);

			const ref = await store.writeTurn(turnBlock);
			pi.appendEntry(COLD_MEMORY_PERSISTENCE_TYPE, {
				version: 1,
				turnId,
				status: "persisted",
				ref,
			});

			// Phase E: extract the agent's teamflow_result from this turn's
			// assistant messages and apply its memory_delta to the rule
			// cache. Guards — a delta is never applied when the result is
			// truncated (finish=length), when the status is not PASS, or
			// when the delta fails structural validation. The rule cache is
			// persisted as a teamflow:rule_cache custom entry, never into
			// the Basic Memory knowledge tree.
			for (const message of messages) {
				if (message.role !== "assistant") continue;
				const resultMatch = message.text.match(/<teamflow_result[\s\S]*?<\/teamflow_result>/);
				if (!resultMatch) continue;
				const resultXml = resultMatch[0];
				// Truncated output (finish=length) must not apply a delta.
				if (/finish\s*=\s*length/.test(resultXml)) continue;
				const statusMatch = resultXml.match(/<status>([^<]*)<\/status>/);
				if (!statusMatch || statusMatch[1].trim() !== "PASS") continue;
				const deltaMatch = resultXml.match(/<memory_delta>[\s\S]*?<\/memory_delta>/);
				if (!deltaMatch) continue;
				const delta: MemoryDelta | null = deserializeRuleDelta(deltaMatch[0]);
				// Structural completeness: validate the delta before reducing.
				if (delta === null || !validateDelta(delta)) continue;
				currentRuleCache = applyDelta(currentRuleCache, delta);
				currentRuleCache.contentHash = computeRuleCacheHash(currentRuleCache);
				pi.appendEntry(RULE_CACHE_CUSTOM_TYPE, {
					version: 1,
					turnId,
					taskId: currentRuleCache.taskId,
					contentHash: currentRuleCache.contentHash,
					xml: serializeRuleCache(currentRuleCache),
				});
			}
		} catch (error) {
			// Never fake success: report the failure explicitly.
			const message = error instanceof Error ? error.message : String(error);
			pi.appendEntry(COLD_MEMORY_PERSISTENCE_TYPE, {
				version: 1,
				turnId,
				status: "failed",
				reason: "MEMORY_PERSISTENCE_FAILED",
				error: message,
			});
		}

		pi.appendEntry(OBSERVATION_CUSTOM_TYPE, receipt);
	});

	// Phase D: hot-zone projection. Keep the latest teamflow:context
	// message (project rules), the latest completed turn, and the active
	// turn; evict older turns from the projection without generating any
	// replacement text — evicted turns stay in the cold store for exact
	// recall.
	pi.on("context", (event, ctx) => {
		const messages = event.messages;

		// teamflow:context custom messages mark turn boundaries.
		const contextIndices: number[] = [];
		for (let i = 0; i < messages.length; i++) {
			const msg = messages[i] as Record<string, unknown>;
			if (isRecord(msg) && msg.customType === CONTEXT_CUSTOM_TYPE) {
				contextIndices.push(i);
			}
		}

		// Fewer than 3 context messages means at most one completed turn:
		// nothing old enough to evict yet, so keep everything.
		if (contextIndices.length < 3) {
			return;
		}

		// The second-to-last context message starts the latest completed
		// turn; everything from there onward is the hot zone (completed
		// turn plus active turn). Earlier turns are evicted.
		const keepFromIndex = contextIndices[contextIndices.length - 2];
		let projected = messages.slice(keepFromIndex);

		// Never split a tool call / tool result causal pair at the
		// eviction boundary: if the first kept message is a toolResult,
		// walk back to the assistant message carrying its toolCall.
		if (keepFromIndex > 0) {
			const firstKept = messages[keepFromIndex] as Record<string, unknown>;
			if (isRecord(firstKept) && firstKept.role === "toolResult") {
				const callId = firstKept.toolCallId;
				for (let j = keepFromIndex - 1; j >= 0; j--) {
					const prevMsg = messages[j] as Record<string, unknown>;
					if (isRecord(prevMsg) && prevMsg.role === "assistant" && hasToolCallId(prevMsg, callId)) {
						// Include the matching tool call in the projection.
						projected = messages.slice(j);
						break;
					}
					if (isRecord(prevMsg) && prevMsg.customType === CONTEXT_CUSTOM_TYPE) {
						// Hit a turn boundary before finding the call — stop.
						break;
					}
				}
			}
		}

		// Phase G: budget check. The protected context must fit the
		// model context window. On overflow, append a structured budget
		// failure receipt instead of letting compaction run. When
		// recalled turn content is present in the projection and pushes
		// the total over budget, classify as RECALL_BUDGET_EXCEEDED;
		// otherwise CONTEXT_BUDGET_EXCEEDED. Both require REPLAN_AND_SPLIT.
		const usage = ctx.getContextUsage();
		if (usage && usage.contextWindow > 0 && usage.tokens !== null) {
			const limit = usage.contextWindow;
			const used = usage.tokens;
			const remaining = limit - used;
			if (remaining < 0) {
				const hasRecalled = projected.some(
					(m) => isRecord(m)
						&& typeof m.customType === "string"
						&& m.customType.startsWith("teamflow:recalled"),
				);
				const reason = hasRecalled
					? RECALL_BUDGET_EXCEEDED
					: CONTEXT_BUDGET_EXCEEDED;
				const entryType = hasRecalled
					? RECALL_BUDGET_EXCEEDED_TYPE
					: BUDGET_EXCEEDED_TYPE;
				const protectedComponent = hasRecalled
					? "project_rules+rule_cache+recalled_turns"
					: "project_rules+rule_cache+latest_turn+active_turn";

				pi.appendEntry(entryType, {
					status: "BLOCKED",
					reason,
					budget: {
						limit,
						used,
						remaining,
					},
					largestSources: [
						{ kind: "project_rules", ref: "AGENTS.md" },
						{ kind: "rule_cache", ref: "cache://adhoc" },
					],
					protectedComponent,
					requiredAction: "REPLAN_AND_SPLIT",
				});
			}
		}

		return { messages: projected };
	});

	// Phase D: hard-cancel all compaction — manual, threshold, and
	// overflow. Hot-zone projection plus the cold store replaces
	// compact-and-retry; losing context to compaction is never
	// acceptable. This is the primary defense against context loss.
	pi.on("session_before_compact", (event, _ctx) => {
		pi.appendEntry(COMPACT_INTERCEPTED_TYPE, {
			reason: event.reason,
			willRetry: event.willRetry,
			cancelled: true,
		});
		return { cancel: true };
	});

	// Phase D: a compact event after session_before_compact cancellation
	// is a runtime invariant violation — record it for diagnosis.
	pi.on("session_compact", (event, _ctx) => {
		pi.appendEntry(COMPACT_VIOLATION_TYPE, {
			reason: event.reason,
			fromExtension: event.fromExtension,
			message:
				"Runtime invariant violation: compaction occurred despite session_before_compact cancellation",
		});
	});
}
