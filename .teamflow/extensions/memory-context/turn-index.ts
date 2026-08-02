import { createHash } from "node:crypto";

// Canonical TurnIndex schema version: 1. The content hash is computed
// over the canonical XML form WITHOUT the content_hash attribute.

export interface IndexSourceRef {
	messageId: string;
	field: string;
	toolCallId?: string;
}

export interface SemanticEntry {
	text: string;
	sources: IndexSourceRef[];
}

export interface TurnIndex {
	version: number;
	blockId: string;
	repository: string;
	taskId: string;
	sessionId: string;
	agent: string;
	sequence: number;
	intent: string;
	actions: SemanticEntry[];
	outcomes: SemanticEntry[];
	decisions: SemanticEntry[];
	constraints: SemanticEntry[];
	failures: SemanticEntry[];
	openQuestions: SemanticEntry[];
	keywords: string[];
	entities: string[];
	artifactRefs: string[];
	sourceEvents: IndexSourceRef[];
	contentHash: string;
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

// Fixed attribute order for the canonical root element. The content_hash
// attribute is appended separately by serialize() and is never part of
// the hash input.
function canonicalAttrsNoHash(index: TurnIndex): string {
	const parts = [
		`version="${index.version}"`,
		`block_id="${escapeXmlAttr(index.blockId ?? "")}"`,
		`repository="${escapeXmlAttr(index.repository ?? "")}"`,
		`task_id="${escapeXmlAttr(index.taskId ?? "")}"`,
		`session_id="${escapeXmlAttr(index.sessionId ?? "")}"`,
		`agent="${escapeXmlAttr(index.agent ?? "")}"`,
		`sequence="${index.sequence}"`,
		`intent="${escapeXmlAttr(index.intent ?? "")}"`,
	];
	return parts.join(" ");
}

function serializeSource(ref: IndexSourceRef): string {
	const attrs = [
		`message_id="${escapeXmlAttr(ref.messageId ?? "")}"`,
		`field="${escapeXmlAttr(ref.field ?? "")}"`,
	];
	if (ref.toolCallId !== undefined) {
		attrs.push(`tool_call_id="${escapeXmlAttr(ref.toolCallId)}"`);
	}
	return `        <source ${attrs.join(" ")}/>`;
}

// Serialize one semantic section (actions, outcomes, ...). Each entry
// carries its text and the list of source references back to the
// original TurnBlock messages/tool events.
function serializeEntrySection(name: string, entries: SemanticEntry[] | undefined): string {
	const lines: string[] = [];
	lines.push(`  <${name}>`);
	for (const entry of entries ?? []) {
		lines.push("    <entry>");
		lines.push(`      <text>${escapeXmlText(entry.text ?? "")}</text>`);
		lines.push("      <sources>");
		for (const ref of entry.sources ?? []) {
			lines.push(serializeSource(ref));
		}
		lines.push("      </sources>");
		lines.push("    </entry>");
	}
	lines.push(`  </${name}>`);
	return lines.join("\n");
}

// Serialize a flat string list section (keywords, entities, artifact_refs).
function serializeListSection(name: string, itemTag: string, items: string[] | undefined): string {
	const lines: string[] = [];
	lines.push(`  <${name}>`);
	for (const item of items ?? []) {
		lines.push(`    <${itemTag}>${escapeXmlText(item ?? "")}</${itemTag}>`);
	}
	lines.push(`  </${name}>`);
	return lines.join("\n");
}

function serializeSourceEvents(events: IndexSourceRef[] | undefined): string {
	const lines: string[] = [];
	lines.push("  <source_events>");
	for (const ref of events ?? []) {
		lines.push(serializeSource(ref));
	}
	lines.push("  </source_events>");
	return lines.join("\n");
}

// Canonical body with a fixed section order so the hash input is stable.
function serializeBody(index: TurnIndex): string {
	return [
		serializeEntrySection("actions", index.actions),
		serializeEntrySection("outcomes", index.outcomes),
		serializeEntrySection("decisions", index.decisions),
		serializeEntrySection("constraints", index.constraints),
		serializeEntrySection("failures", index.failures),
		serializeEntrySection("open_questions", index.openQuestions),
		serializeListSection("keywords", "keyword", index.keywords),
		serializeListSection("entities", "entity", index.entities),
		serializeListSection("artifact_refs", "ref", index.artifactRefs),
		serializeSourceEvents(index.sourceEvents),
	].join("\n");
}

// Canonical XML form WITHOUT the content_hash attribute; used as the
// hash input and as the basis for serialize().
function serializeCanonicalNoHash(index: TurnIndex): string {
	return `<teamflow_turn_index ${canonicalAttrsNoHash(index)}>\n${serializeBody(index)}\n</teamflow_turn_index>`;
}

export function computeContentHash(index: TurnIndex): string {
	const xml = serializeCanonicalNoHash(index);
	return "sha256:" + createHash("sha256").update(xml).digest("hex");
}

export function serialize(index: TurnIndex): string {
	const hash = computeContentHash(index);
	return `<teamflow_turn_index ${canonicalAttrsNoHash(index)} content_hash="${hash}">\n${serializeBody(index)}\n</teamflow_turn_index>`;
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

function parseSource(attrStr: string): IndexSourceRef {
	const attrs = parseAttrs(attrStr);
	const ref: IndexSourceRef = {
		messageId: attrs["message_id"] ?? "",
		field: attrs["field"] ?? "",
	};
	if (attrs["tool_call_id"] !== undefined) ref.toolCallId = attrs["tool_call_id"];
	return ref;
}

function parseEntrySection(xml: string, name: string): SemanticEntry[] {
	const sectionMatch = xml.match(new RegExp(`<${name}>([\\s\\S]*?)</${name}>`));
	if (!sectionMatch) return [];
	const body = sectionMatch[1];
	const entries: SemanticEntry[] = [];
	const entryRegex = /<entry>([\s\S]*?)<\/entry>/g;
	let em: RegExpExecArray | null;
	while ((em = entryRegex.exec(body)) !== null) {
		const entryBody = em[1];
		const textMatch = entryBody.match(/<text>([\s\S]*?)<\/text>/);
		const sources: IndexSourceRef[] = [];
		const sourceRegex = /<source\s+([^>]*?)\/>/g;
		let sm: RegExpExecArray | null;
		while ((sm = sourceRegex.exec(entryBody)) !== null) {
			sources.push(parseSource(sm[1]));
		}
		entries.push({
			text: textMatch ? unescapeXmlText(textMatch[1]) : "",
			sources,
		});
	}
	return entries;
}

function parseListSection(xml: string, name: string, itemTag: string): string[] {
	const sectionMatch = xml.match(new RegExp(`<${name}>([\\s\\S]*?)</${name}>`));
	if (!sectionMatch) return [];
	const body = sectionMatch[1];
	const items: string[] = [];
	const itemRegex = new RegExp(`<${itemTag}>([\\s\\S]*?)</${itemTag}>`, "g");
	let m: RegExpExecArray | null;
	while ((m = itemRegex.exec(body)) !== null) {
		items.push(unescapeXmlText(m[1]));
	}
	return items;
}

function parseSourceEvents(xml: string): IndexSourceRef[] {
	const sectionMatch = xml.match(/<source_events>([\s\S]*?)<\/source_events>/);
	if (!sectionMatch) return [];
	const events: IndexSourceRef[] = [];
	const sourceRegex = /<source\s+([^>]*?)\/>/g;
	let m: RegExpExecArray | null;
	while ((m = sourceRegex.exec(sectionMatch[1])) !== null) {
		events.push(parseSource(m[1]));
	}
	return events;
}

export function deserialize(xml: string): TurnIndex {
	const match = xml.match(/<teamflow_turn_index\s+(.*?)>/s);
	if (!match) throw new Error("Invalid teamflow_turn_index XML");
	const attrs = parseAttrs(match[1]);

	return {
		version: parseInt(attrs["version"], 10),
		blockId: attrs["block_id"] ?? "",
		repository: attrs["repository"] ?? "",
		taskId: attrs["task_id"] ?? "",
		sessionId: attrs["session_id"] ?? "",
		agent: attrs["agent"] ?? "",
		sequence: parseInt(attrs["sequence"], 10),
		intent: attrs["intent"] ?? "",
		actions: parseEntrySection(xml, "actions"),
		outcomes: parseEntrySection(xml, "outcomes"),
		decisions: parseEntrySection(xml, "decisions"),
		constraints: parseEntrySection(xml, "constraints"),
		failures: parseEntrySection(xml, "failures"),
		openQuestions: parseEntrySection(xml, "open_questions"),
		keywords: parseListSection(xml, "keywords", "keyword"),
		entities: parseListSection(xml, "entities", "entity"),
		artifactRefs: parseListSection(xml, "artifact_refs", "ref"),
		sourceEvents: parseSourceEvents(xml),
		contentHash: attrs["content_hash"] || "",
	};
}

// Structural validation first, hash validation last: a structurally
// invalid index is rejected without trusting or recomputing its hash.
export function validateIndex(index: unknown): boolean {
	if (typeof index !== "object" || index === null || Array.isArray(index)) return false;
	const idx = index as TurnIndex;
	if (idx.version !== 1) return false;
	if (typeof idx.blockId !== "string" || idx.blockId.length === 0) return false;
	if (typeof idx.repository !== "string") return false;
	if (typeof idx.taskId !== "string") return false;
	if (typeof idx.sessionId !== "string") return false;
	if (typeof idx.agent !== "string") return false;
	if (typeof idx.sequence !== "number") return false;
	if (typeof idx.intent !== "string") return false;
	const arrayFields: (keyof TurnIndex)[] = [
		"actions",
		"outcomes",
		"decisions",
		"constraints",
		"failures",
		"openQuestions",
		"keywords",
		"entities",
		"artifactRefs",
		"sourceEvents",
	];
	for (const field of arrayFields) {
		if (!Array.isArray(idx[field])) return false;
	}
	if (typeof idx.contentHash !== "string") return false;
	return idx.contentHash === computeContentHash(idx);
}
