/**
 * opencode API types.
 *
 * Derived from the live contract of a running `opencode serve` (see
 * docs/teamflow-web-console-design.md section 6), not from hand-written
 * guesses. Shared with the front end so a schema change breaks the type
 * check in one place rather than silently at runtime.
 *
 * Only the subset the console consumes is modelled. Fields opencode may add
 * are tolerated: every interface is treated as open, and unknown `Part`
 * types fall through to `UnknownPart`.
 */

export interface SessionModel {
  id: string;
  providerID: string;
  variant: string;
}

export interface SessionTokens {
  input: number;
  output: number;
  reasoning: number;
  cache: { read: number; write: number };
}

export interface Session {
  id: string;
  slug: string;
  projectID: string;
  directory: string;
  path: string;
  title: string;
  agent: string;
  version: string;
  model: SessionModel;
  summary: { additions: number; deletions: number; files: number };
  tokens: SessionTokens;
  cost: number;
  time: { created: number; updated: number };
}

export type MessageRole = "user" | "assistant";

export interface MessageInfo {
  id: string;
  sessionID: string;
  role: MessageRole;
  agent?: string;
  model?: { providerID: string; modelID: string };
  time: { created: number; completed?: number };
}

interface PartBase {
  id: string;
  sessionID: string;
  messageID: string;
}

export interface TextPart extends PartBase {
  type: "text";
  text: string;
}

export interface ReasoningPart extends PartBase {
  type: "reasoning";
  text: string;
  time?: { start: number; end?: number };
}

export interface ToolPart extends PartBase {
  type: "tool";
  callID: string;
  tool: string;
  /** Shape varies by tool and by lifecycle stage; narrowed at the call site. */
  state: Record<string, unknown>;
}

export interface StepStartPart extends PartBase {
  type: "step-start";
  snapshot?: string;
}

export interface StepFinishPart extends PartBase {
  type: "step-finish";
  reason: string;
  snapshot?: string;
  cost: number;
  tokens: SessionTokens;
}

export interface PatchPart extends PartBase {
  type: "patch";
}

/** Forward compatibility: a new upstream part type must not break parsing. */
export interface UnknownPart extends PartBase {
  type: string;
}

export type Part =
  | TextPart
  | ReasoningPart
  | ToolPart
  | StepStartPart
  | StepFinishPart
  | PatchPart
  | UnknownPart;

export interface Message {
  info: MessageInfo;
  parts: Part[];
}

/**
 * Part types the console renders in Phase D. `step-start`, `step-finish`,
 * and `patch` are intentionally excluded: they are execution bookkeeping,
 * not conversation content.
 */
export const RENDERED_PART_TYPES = ["text", "reasoning", "tool"] as const;

export type RenderedPart = TextPart | ReasoningPart | ToolPart;

export function isRenderedPart(part: Part): part is RenderedPart {
  return (RENDERED_PART_TYPES as readonly string[]).includes(part.type);
}

/** An SSE frame from `GET /event`. */
export interface OpencodeEvent {
  id: string;
  type: string;
  properties: Record<string, unknown>;
}
