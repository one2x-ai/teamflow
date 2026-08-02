/**
 * Teamflow memory-context extension — Phase E: rule cache (准则 cache).
 *
 * The rule cache is the long-lived constraint layer: project/role
 * boundaries, user constraints, acceptance criteria, accepted
 * decisions, explicit non-goals, and safety requirements. Rules are
 * never evicted from the visible context and are never deleted —
 * supersede/retire only change status, keeping old rules auditable.
 *
 * This module defines the schema (Rule / RuleCache / MemoryDelta), the
 * canonical XML serialization with SHA-256 content hashing (mirroring
 * the turn-block.ts pattern), the <memory_delta> incremental format
 * (assert / supersede / retire), and validateDelta() for structural
 * completeness checking. The authority-aware incremental reducer lives
 * in rule-cache-reducer.ts.
 */

import { createHash } from "node:crypto";

// Authority levels with numeric rank for comparison. A lower-rank
// source can never override, supersede, or retire a higher-rank rule.
export const AUTHORITY_RANK: Record<string, number> = {
	repository: 5,
	system_policy: 5,
	user: 4,
	planner: 3,
	tool_evidence: 2,
	candidate: 1,
};

export interface Rule {
	id: string;
	key: string;
	kind: string; // constraint, decision, fact, non-goal, inference, evidence, ...
	authority: string; // repository, system_policy, user, planner, tool_evidence, candidate
	status: string; // active, candidate, superseded, retired
	scope: string; // task, project, session, global
	source: string; // reference to the original event, e.g. "memory://turn-18#u1"
	content: string;
	contentHash: string;
}

export interface RuleCache {
	version: number;
	taskId: string;
	rules: Rule[];
	contentHash: string;
}

export interface DeltaOperation {
	op: "assert" | "supersede" | "retire";
	key: string;
	kind?: string;
	authority: string;
	scope?: string;
	source: string;
	content?: string;
}

export interface MemoryDelta {
	operations: DeltaOperation[];
}

function escapeXmlAttr(value: string): string {
	return value
		.replace(/&/g, "&amp;")
		.replace(/</g, "&lt;")
		.replace(/>/g, "&gt;")
		.replace(/"/g, "&quot;")
		.replace(/'/g, "&apos;");
}

function unescapeXmlAttr(value: string): string {
	return value
		.replace(/&amp;/g, "&")
		.replace(/&lt;/g, "<")
		.replace(/&gt;/g, ">")
		.replace(/&quot;/g, '"')
		.replace(/&apos;/g, "'");
}

function escapeXmlText(value: string): string {
	return value
		.replace(/&/g, "&amp;")
		.replace(/</g, "&lt;")
		.replace(/>/g, "&gt;");
}

function unescapeXmlText(value: string): string {
	return value
		.replace(/&amp;/g, "&")
		.replace(/&lt;/g, "<")
		.replace(/&gt;/g, ">");
}

function parseAttrs(attrStr: string): Record<string, string> {
	const attrs: Record<string, string> = {};
	const attrRegex = /([a-z_]+)="((?:[^"]|&quot;)*)"/g;
	let m: RegExpExecArray | null;
	while ((m = attrRegex.exec(attrStr)) !== null) {
		attrs[m[1]] = unescapeXmlAttr(m[2]);
	}
	return attrs;
}

// Canonical rule attribute list WITHOUT content_hash (hash input).
function canonicalRuleAttrsNoHash(rule: Rule): string {
	const parts = [
		`id="${escapeXmlAttr(rule.id)}"`,
		`key="${escapeXmlAttr(rule.key)}"`,
		`kind="${escapeXmlAttr(rule.kind)}"`,
		`authority="${escapeXmlAttr(rule.authority)}"`,
		`status="${escapeXmlAttr(rule.status)}"`,
		`scope="${escapeXmlAttr(rule.scope)}"`,
		`source="${escapeXmlAttr(rule.source)}"`,
	];
	return parts.join(" ");
}

function serializeRuleCanonicalNoHash(rule: Rule): string {
	return `  <rule ${canonicalRuleAttrsNoHash(rule)}>${escapeXmlText(rule.content)}</rule>`;
}

// Per-rule content hash over the rule's canonical form without its own
// content_hash attribute.
export function computeRuleContentHash(rule: Rule): string {
	const xml = serializeRuleCanonicalNoHash(rule);
	return "sha256:" + createHash("sha256").update(xml).digest("hex");
}

function serializeRule(rule: Rule): string {
	const hash = computeRuleContentHash(rule);
	return `  <rule ${canonicalRuleAttrsNoHash(rule)} content_hash="${hash}">${escapeXmlText(rule.content)}</rule>`;
}

// Canonical XML form WITHOUT the root content_hash attribute; used as
// the hash input and as the basis for serialize(). Rule-level
// content_hash attributes are always recomputed so stale input values
// cannot affect the canonical form.
function serializeCanonicalNoHash(cache: RuleCache): string {
	const lines: string[] = [];
	lines.push(`<rule_cache version="${cache.version}" task_id="${escapeXmlAttr(cache.taskId)}">`);
	for (const rule of cache.rules ?? []) {
		lines.push(serializeRule(rule));
	}
	lines.push("</rule_cache>");
	return lines.join("\n");
}

// Deterministic SHA-256 over the canonical form. The cache-level
// contentHash field is excluded, so it never affects the output.
export function computeContentHash(cache: RuleCache): string {
	const xml = serializeCanonicalNoHash(cache);
	return "sha256:" + createHash("sha256").update(xml).digest("hex");
}

export function serialize(cache: RuleCache): string {
	const hash = computeContentHash(cache);
	const lines: string[] = [];
	lines.push(
		`<rule_cache version="${cache.version}" task_id="${escapeXmlAttr(cache.taskId)}" content_hash="${hash}">`,
	);
	for (const rule of cache.rules ?? []) {
		lines.push(serializeRule(rule));
	}
	lines.push("</rule_cache>");
	return lines.join("\n");
}

export function deserialize(xml: string): RuleCache {
	const match = xml.match(/<rule_cache\s+(.*?)>/s);
	if (!match) throw new Error("Invalid rule_cache XML");
	const attrs = parseAttrs(match[1]);

	const rules: Rule[] = [];
	const ruleRegex = /<rule\s+([^>]*)>([\s\S]*?)<\/rule>/g;
	let rm: RegExpExecArray | null;
	while ((rm = ruleRegex.exec(xml)) !== null) {
		const ruleAttrs = parseAttrs(rm[1]);
		rules.push({
			id: ruleAttrs["id"] ?? "",
			key: ruleAttrs["key"] ?? "",
			kind: ruleAttrs["kind"] ?? "",
			authority: ruleAttrs["authority"] ?? "",
			status: ruleAttrs["status"] ?? "",
			scope: ruleAttrs["scope"] ?? "",
			source: ruleAttrs["source"] ?? "",
			content: unescapeXmlText(rm[2]),
			contentHash: ruleAttrs["content_hash"] ?? "",
		});
	}

	return {
		version: parseInt(attrs["version"], 10),
		taskId: attrs["task_id"] ?? "",
		rules,
		contentHash: attrs["content_hash"] || "",
	};
}

// Serialize the delta attributes shared by assert and supersede rule
// elements inside <memory_delta>.
function deltaRuleAttrs(op: DeltaOperation): string {
	const parts = [`key="${escapeXmlAttr(op.key)}"`];
	if (op.kind !== undefined) parts.push(`kind="${escapeXmlAttr(op.kind)}"`);
	parts.push(`authority="${escapeXmlAttr(op.authority)}"`);
	if (op.scope !== undefined) parts.push(`scope="${escapeXmlAttr(op.scope)}"`);
	parts.push(`source="${escapeXmlAttr(op.source)}"`);
	return parts.join(" ");
}

// Canonical <memory_delta> XML: assert/supersede embed a <rule>
// element; retire is a self-closing element identifying the rule.
export function serializeRuleDelta(delta: MemoryDelta): string {
	const lines: string[] = [];
	lines.push("<memory_delta>");
	for (const op of delta.operations ?? []) {
		if (op.op === "assert") {
			lines.push(`  <assert><rule ${deltaRuleAttrs(op)}>${escapeXmlText(op.content ?? "")}</rule></assert>`);
		} else if (op.op === "supersede") {
			lines.push(`  <supersede><rule ${deltaRuleAttrs(op)}>${escapeXmlText(op.content ?? "")}</rule></supersede>`);
		} else if (op.op === "retire") {
			lines.push(
				`  <retire key="${escapeXmlAttr(op.key)}" authority="${escapeXmlAttr(op.authority)}" source="${escapeXmlAttr(op.source)}" />`,
			);
		}
	}
	lines.push("</memory_delta>");
	return lines.join("\n");
}

// Parse a <memory_delta> XML document back into a MemoryDelta. Returns
// null when the document has no memory_delta root; individual malformed
// operations are skipped so the parser stays total.
export function deserializeRuleDelta(xml: string): MemoryDelta | null {
	if (!/<memory_delta[\s>]/.test(xml)) return null;
	const operations: DeltaOperation[] = [];

	const wrappedRegex = /<(assert|supersede)>\s*<rule\s+([^>]*)>([\s\S]*?)<\/rule>\s*<\/\1>/g;
	let wm: RegExpExecArray | null;
	while ((wm = wrappedRegex.exec(xml)) !== null) {
		const attrs = parseAttrs(wm[2]);
		operations.push({
			op: wm[1] as "assert" | "supersede",
			key: attrs["key"] ?? "",
			kind: attrs["kind"],
			authority: attrs["authority"] ?? "",
			scope: attrs["scope"],
			source: attrs["source"] ?? "",
			content: unescapeXmlText(wm[3]),
		});
	}

	const retireRegex = /<retire\s+([^>]*?)\/>/g;
	let rm: RegExpExecArray | null;
	while ((rm = retireRegex.exec(xml)) !== null) {
		const attrs = parseAttrs(rm[1]);
		operations.push({
			op: "retire",
			key: attrs["key"] ?? "",
			authority: attrs["authority"] ?? "",
			source: attrs["source"] ?? "",
		});
	}

	return { operations };
}

function isRecord(value: unknown): value is Record<string, unknown> {
	return typeof value === "object" && value !== null;
}

// Structural completeness guard: a delta is valid only when it carries
// an operations array and every operation has a valid op plus the
// required fields for that op. Invalid structures are rejected before
// the reducer ever sees them.
export function validateDelta(delta: unknown): boolean {
	if (!isRecord(delta)) return false;
	if (!Array.isArray(delta.operations)) return false;
	for (const op of delta.operations) {
		if (!isRecord(op)) return false;
		if (op.op !== "assert" && op.op !== "supersede" && op.op !== "retire") return false;
		if (typeof op.key !== "string" || op.key === "") return false;
		if (typeof op.authority !== "string" || op.authority === "") return false;
		if (typeof op.source !== "string") return false;
		if (op.op === "assert" || op.op === "supersede") {
			if (op.content !== undefined && typeof op.content !== "string") return false;
			if (op.kind !== undefined && typeof op.kind !== "string") return false;
			if (op.scope !== undefined && typeof op.scope !== "string") return false;
		}
	}
	return true;
}
