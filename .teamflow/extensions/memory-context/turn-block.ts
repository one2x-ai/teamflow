import { createHash } from "node:crypto";

export interface TurnToolCall {
	id: string;
	name: string;
	arguments: string;
}

export interface TurnMessage {
	id: string;
	role: "user" | "assistant" | "toolResult";
	text: string;
	toolCalls?: TurnToolCall[];
	callId?: string;
	status?: string;
}

export interface TurnBlock {
	version: number;
	id: string;
	sequence: number;
	previous: string | null;
	repository: string;
	taskId: string;
	sessionId: string;
	agent: string;
	startedAt: string;
	settledAt: string;
	contentHash: string;
	// Optional for backward compatibility with metadata-only blocks.
	messages?: TurnMessage[];
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

// Build canonical attribute list WITHOUT content_hash (used for hash computation)
function canonicalAttrsNoHash(block: TurnBlock): string {
	const parts = [
		`version="${block.version}"`,
		`id="${escapeXmlAttr(block.id)}"`,
		`sequence="${block.sequence}"`,
		`previous="${block.previous === null ? "" : escapeXmlAttr(block.previous)}"`,
		`repository="${escapeXmlAttr(block.repository)}"`,
		`task_id="${escapeXmlAttr(block.taskId)}"`,
		`session_id="${escapeXmlAttr(block.sessionId)}"`,
		`agent="${escapeXmlAttr(block.agent)}"`,
		`started_at="${escapeXmlAttr(block.startedAt)}"`,
		`settled_at="${escapeXmlAttr(block.settledAt)}"`,
	];
	return parts.join(" ");
}

// Serialize the <messages> body. Empty/missing messages still produce an
// (empty) <messages> element so the canonical form is stable.
function serializeMessages(messages: TurnMessage[] | undefined): string {
	const lines: string[] = [];
	lines.push("  <messages>");
	for (const message of messages ?? []) {
		const attrs = [`id="${escapeXmlAttr(message.id)}"`, `role="${message.role}"`];
		if (message.role === "toolResult") {
			attrs.push(`call_id="${escapeXmlAttr(message.callId ?? "")}"`);
			attrs.push(`status="${escapeXmlAttr(message.status ?? "")}"`);
		}
		const parts: string[] = [];
		parts.push(`      <text>${escapeXmlText(message.text)}</text>`);
		for (const call of message.toolCalls ?? []) {
			parts.push(
				`      <tool_call id="${escapeXmlAttr(call.id)}" name="${escapeXmlAttr(call.name)}">${escapeXmlText(call.arguments)}</tool_call>`,
			);
		}
		lines.push(`    <message ${attrs.join(" ")}>`);
		lines.push(parts.join("\n"));
		lines.push("    </message>");
	}
	lines.push("  </messages>");
	return lines.join("\n");
}

// Canonical XML form WITHOUT the content_hash attribute; used as the
// hash input and as the basis for serialize().
function serializeCanonicalNoHash(block: TurnBlock): string {
	return `<teamflow_turn ${canonicalAttrsNoHash(block)}>\n${serializeMessages(block.messages)}\n</teamflow_turn>`;
}

export function computeContentHash(block: TurnBlock): string {
	const xml = serializeCanonicalNoHash(block);
	return "sha256:" + createHash("sha256").update(xml).digest("hex");
}

export function serialize(block: TurnBlock): string {
	const hash = computeContentHash(block);
	return `<teamflow_turn ${canonicalAttrsNoHash(block)} content_hash="${hash}">\n${serializeMessages(block.messages)}\n</teamflow_turn>`;
}

export function deserialize(xml: string): TurnBlock {
	// Handles both the legacy self-closing form `<teamflow_turn attrs />`
	// (the captured attrs string ends with a harmless trailing "/") and the
	// new body form `<teamflow_turn attrs> ... </teamflow_turn>`.
	const match = xml.match(/<teamflow_turn\s+(.*?)>/s);
	if (!match) throw new Error("Invalid teamflow_turn XML");
	const attrStr = match[1];
	const attrs: Record<string, string> = {};
	const attrRegex = /([a-z_]+)="((?:[^"]|&quot;)*)"/g;
	let m: RegExpExecArray | null;
	while ((m = attrRegex.exec(attrStr)) !== null) {
		attrs[m[1]] = unescapeXmlAttr(m[2]);
	}

	const block: TurnBlock = {
		version: parseInt(attrs["version"], 10),
		id: attrs["id"],
		sequence: parseInt(attrs["sequence"], 10),
		previous: attrs["previous"] === "" ? null : attrs["previous"],
		repository: attrs["repository"],
		taskId: attrs["task_id"],
		sessionId: attrs["session_id"],
		agent: attrs["agent"],
		startedAt: attrs["started_at"],
		settledAt: attrs["settled_at"],
		contentHash: attrs["content_hash"] || "",
	};

	// Parse <message> elements from the body, if any.
	const messages: TurnMessage[] = [];
	const messageRegex = /<message\s+([^>]*)>([\s\S]*?)<\/message>/g;
	let mm: RegExpExecArray | null;
	while ((mm = messageRegex.exec(xml)) !== null) {
		const msgAttrStr = mm[1];
		const msgBody = mm[2];
		const msgAttrs: Record<string, string> = {};
		const msgAttrRegex = /([a-z_]+)="((?:[^"]|&quot;)*)"/g;
		let ma: RegExpExecArray | null;
		while ((ma = msgAttrRegex.exec(msgAttrStr)) !== null) {
			msgAttrs[ma[1]] = unescapeXmlAttr(ma[2]);
		}

		const message: TurnMessage = {
			id: msgAttrs["id"],
			role: msgAttrs["role"] as TurnMessage["role"],
			text: "",
		};
		if (msgAttrs["call_id"] !== undefined) message.callId = msgAttrs["call_id"];
		if (msgAttrs["status"] !== undefined) message.status = msgAttrs["status"];

		const textMatch = msgBody.match(/<text>([\s\S]*?)<\/text>/);
		if (textMatch) message.text = unescapeXmlText(textMatch[1]);

		const toolCalls: TurnToolCall[] = [];
		const toolCallRegex = /<tool_call\s+([^>]*)>([\s\S]*?)<\/tool_call>/g;
		let tc: RegExpExecArray | null;
		while ((tc = toolCallRegex.exec(msgBody)) !== null) {
			const tcAttrStr = tc[1];
			const tcAttrs: Record<string, string> = {};
			const tcAttrRegex = /([a-z_]+)="((?:[^"]|&quot;)*)"/g;
			let ta: RegExpExecArray | null;
			while ((ta = tcAttrRegex.exec(tcAttrStr)) !== null) {
				tcAttrs[ta[1]] = unescapeXmlAttr(ta[2]);
			}
			toolCalls.push({
				id: tcAttrs["id"],
				name: tcAttrs["name"],
				arguments: unescapeXmlText(tc[2]),
			});
		}
		if (toolCalls.length > 0) message.toolCalls = toolCalls;

		messages.push(message);
	}
	if (messages.length > 0) block.messages = messages;

	return block;
}

// Redact known secret patterns from message text before persistence.
// Best-effort hygiene filter, not a security boundary.
export function redactSecrets(text: string): string {
	return text
		// Private key blocks (multi-line, must come first)
		.replace(/-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----/g, "[REDACTED]")
		// Bearer tokens
		.replace(/Bearer [a-zA-Z0-9._-]+/g, "[REDACTED]")
		// API keys (sk- prefix, 20+ alphanumeric chars)
		.replace(/sk-[a-zA-Z0-9]{20,}/g, "[REDACTED]")
		// Key-value assignments (password=, api_key:, SECRET=, etc.)
		.replace(/(?:password|api_key|apikey|secret|token)\s*[:=]\s*[^\s]+/gi, "[REDACTED]");
}
