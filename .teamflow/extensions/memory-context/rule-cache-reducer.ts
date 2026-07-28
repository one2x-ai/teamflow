/**
 * Teamflow memory-context extension — Phase E: rule cache reducer.
 *
 * applyDelta() is the authority-aware incremental reducer for the rule
 * cache (准则 cache). Semantics:
 *
 * - Pure: the input cache and delta are never mutated; a new RuleCache
 *   is always returned.
 * - Total: malformed operations are silently skipped; the function
 *   never throws.
 * - Incremental: rules not mentioned by any operation are preserved
 *   unchanged — omission never deletes.
 * - Authority-ordered: a lower-rank source can never update, supersede,
 *   or retire a higher-rank rule. Unknown authority ranks as candidate.
 * - Candidate-only inference: rules asserted with candidate authority
 *   enter with status "candidate", never "active".
 * - Tool evidence must reference the original event: a tool_evidence
 *   assert with an empty source is rejected.
 * - supersede/retire keep the old rule in the cache for audit; only
 *   its status changes.
 */

import type { RuleCache, MemoryDelta, DeltaOperation, Rule } from "./rule-cache";
import { AUTHORITY_RANK, computeContentHash } from "./rule-cache";

// Unknown authority defaults to the candidate rank, the lowest level.
export function authorityRank(authority: string): number {
	return AUTHORITY_RANK[authority] ?? 1;
}

// A rule is addressable by delta operations while it is active or still
// a candidate; superseded and retired rules are audit-only.
function isLiveStatus(status: string): boolean {
	return status === "active" || status === "candidate";
}

function statusForAuthority(authority: string): string {
	return authority === "candidate" ? "candidate" : "active";
}

function asString(value: unknown, fallback: string): string {
	return typeof value === "string" ? value : fallback;
}

// Deterministic sequential id. Determinism is required for replayable
// receipts: the same delta sequence must yield the same cache state.
function nextRuleId(rules: Rule[]): string {
	return `rule-${rules.length + 1}`;
}

export function applyDelta(cache: RuleCache, delta: MemoryDelta): RuleCache {
	// Deep-copy the rules so the input cache is never mutated.
	const rules: Rule[] = (cache?.rules ?? []).map((rule) => ({ ...rule }));
	const operations = Array.isArray(delta?.operations) ? delta.operations : [];

	for (const op of operations) {
		if (op === null || typeof op !== "object") continue;
		const key = asString(op.key, "");
		if (key === "") continue;
		const authority = asString(op.authority, "candidate");
		const source = asString(op.source, "");
		const newRank = authorityRank(authority);
		const existing = rules.find((rule) => rule.key === key && isLiveStatus(rule.status));

		if (op.op === "assert") {
			const content = asString(op.content, "");
			const kind = asString(op.kind, "fact");
			const scope = asString(op.scope, "task");
			if (!existing) {
				// tool_evidence must reference the original event.
				if (authority === "tool_evidence" && source === "") continue;
				rules.push({
					id: nextRuleId(rules),
					key,
					kind,
					authority,
					status: statusForAuthority(authority),
					scope,
					source,
					content,
					contentHash: "",
				});
			} else {
				// Lower authority may not override an existing rule.
				if (newRank < authorityRank(existing.authority)) continue;
				existing.content = content;
				existing.authority = authority;
				existing.source = source;
				existing.kind = kind;
				existing.status = statusForAuthority(authority);
			}
		} else if (op.op === "supersede") {
			if (!existing) continue;
			if (newRank < authorityRank(existing.authority)) continue;
			// Keep the old rule for audit; only its status changes.
			existing.status = "superseded";
			rules.push({
				id: nextRuleId(rules),
				key,
				kind: asString(op.kind, existing.kind),
				authority,
				status: statusForAuthority(authority),
				scope: asString(op.scope, existing.scope),
				source,
				content: asString(op.content, ""),
				contentHash: "",
			});
		} else if (op.op === "retire") {
			if (!existing) continue;
			if (newRank < authorityRank(existing.authority)) continue;
			existing.status = "retired";
		}
	}

	const result: RuleCache = {
		version: typeof cache?.version === "number" ? cache.version : 1,
		taskId: typeof cache?.taskId === "string" ? cache.taskId : "adhoc",
		rules,
		contentHash: "",
	};
	result.contentHash = computeContentHash(result);
	return result;
}
