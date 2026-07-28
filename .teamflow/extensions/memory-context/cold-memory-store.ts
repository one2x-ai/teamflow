import type { TurnBlock } from "./turn-block";
import type { TurnIndex } from "./turn-index";

export type MemoryRef = string;

export interface SessionScope {
	repository: string;
	taskId: string;
	sessionId: string;
	agent: string;
}

export interface MemoryScope {
	repository: string;
	taskId?: string;
	sessionId?: string;
}

export interface SearchHit {
	blockRef: string;
	blockId: string;
	sequence: number;
	repository: string;
	taskId: string;
	sessionId: string;
	agent: string;
	score: number;
	matchedFields: string[];
}

export interface SearchOptions {
	limit?: number;
}

export interface ColdMemoryStore {
	writeTurn(turn: TurnBlock): Promise<MemoryRef>;
	writeIndex(index: TurnIndex): Promise<void>;
	readTurn(ref: MemoryRef): Promise<TurnBlock>;
	readByOffset(scope: SessionScope, before: number, count?: number): Promise<TurnBlock[]>;
	search(query: string, scope: MemoryScope, options?: SearchOptions): Promise<SearchHit[]>;
}
