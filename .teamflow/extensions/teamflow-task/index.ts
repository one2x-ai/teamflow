/**
 * Teamflow task role launcher.
 *
 * Registers `task(agent, prompt)` and `task_group(tasks, max_concurrency)`
 * tools only on depth-0 roles whose frontmatter declares `delegates: true`.
 * Each
 * call spawns isolated `pi` children in JSON mode whose system prompt is the
 * Markdown body of `.teamflow/agents/<role>.md`, discovered by filename.
 * Frontmatter is parsed with Pi's parseFrontmatter; `model` must be
 * "<provider>/<model>", while `description`, `tools`, and the strict boolean
 * `delegates` permission are optional. The Markdown agent files are the sole
 * source of truth for role identity.
 */

import { spawn } from "node:child_process";
import * as fs from "node:fs";
import * as path from "node:path";
import { fileURLToPath } from "node:url";
import type { AgentToolResult } from "@earendil-works/pi-agent-core";
import { type ExtensionAPI, parseFrontmatter } from "@earendil-works/pi-coding-agent";
import { Type } from "typebox";

// .teamflow/extensions/teamflow-task/index.ts -> .teamflow/agents
const AGENTS_DIR = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..", "..", "agents");
const ROLE_NAME_PATTERN = /^[A-Za-z0-9][A-Za-z0-9_-]*$/;
const MAX_CONCURRENCY = 8;

interface TaskDetails {
	agent: string;
	exitCode: number;
	stopReason?: string;
	stderr: string;
}

interface ResolvedRole {
	provider: string;
	model: string;
	tools: string[];
	body: string;
}

interface RoleRunResult {
	agent: string;
	success: boolean;
	content: string;
	exitCode: number;
	stopReason?: string;
	stderr: string;
}

function listAvailableRoles(): string {
	if (!fs.existsSync(AGENTS_DIR)) return "none";
	const roles = fs
		.readdirSync(AGENTS_DIR)
		.filter((name) => name.endsWith(".md"))
		.map((name) => name.slice(0, -3))
		.sort();
	return roles.length > 0 ? roles.join(", ") : "none";
}

const delegatesCache = new Map<string, boolean>();

function roleMayDelegate(role: string | undefined, depth: number): boolean {
	if (!role || depth !== 0 || !ROLE_NAME_PATTERN.test(role)) return false;
	const cached = delegatesCache.get(role);
	if (cached !== undefined) return cached;
	const agentPath = path.join(AGENTS_DIR, `${role}.md`);
	if (!fs.existsSync(agentPath)) return false;
	const { frontmatter } = parseFrontmatter<Record<string, unknown>>(
		fs.readFileSync(agentPath, "utf-8"),
	);
	const result = frontmatter.delegates === true;
	delegatesCache.set(role, result);
	return result;
}

function getPiInvocation(args: string[]): { command: string; args: string[] } {
	const currentScript = process.argv[1];
	const isBunVirtualScript = currentScript?.startsWith("/$bunfs/root/");
	if (currentScript && !isBunVirtualScript && fs.existsSync(currentScript)) {
		return { command: process.execPath, args: [currentScript, ...args] };
	}

	const execName = path.basename(process.execPath).toLowerCase();
	if (!/^(node|bun)(\.exe)?$/.test(execName)) {
		return { command: process.execPath, args };
	}
	return { command: "pi", args };
}

function resolveRole(agent: string): { ok: true; role: string; resolved: ResolvedRole } | { ok: false; role: string; error: string } {
	const role = agent.trim();
	if (!ROLE_NAME_PATTERN.test(role)) {
		return {
			ok: false,
			role,
			error: `Invalid role name: "${agent}". Roles are discovered by filename in .teamflow/agents/.`,
		};
	}

	const agentPath = path.join(AGENTS_DIR, `${role}.md`);
	if (!fs.existsSync(agentPath)) {
		return { ok: false, role, error: `Unknown role: "${role}". Available roles: ${listAvailableRoles()}.` };
	}

	const { frontmatter, body } = parseFrontmatter<Record<string, unknown>>(
		fs.readFileSync(agentPath, "utf-8"),
	);

	const modelValue = String(frontmatter.model ?? "");
	const [provider, model] = modelValue.split("/");
	if (!provider || !model) {
		return {
			ok: false,
			role,
			error: `Role "${role}" is invalid: frontmatter model must be "<provider>/<model>", got "${modelValue}".`,
		};
	}

	const tools = String(frontmatter.tools ?? "")
		.split(",")
		.map((tool) => tool.trim())
		.filter(Boolean);

	return { ok: true, role, resolved: { provider, model, tools, body } };
}

/**
 * Spawn one isolated pi child for a role and collect its final assistant text.
 * On signal abort the child is killed (SIGTERM, then SIGKILL) so no orphan
 * process is left running.
 */
async function runRoleChild(
	agent: string,
	prompt: string,
	signal: AbortSignal | undefined,
	cwd: string,
): Promise<RoleRunResult> {
	const resolution = resolveRole(agent);
	if (!resolution.ok) {
		return { agent: resolution.role, success: false, content: resolution.error, exitCode: 1, stderr: "" };
	}
	const { role, resolved } = resolution;
	const stderr = { text: "" };

	const args = [
		"--mode",
		"json",
		"--no-session",
		"--provider",
		resolved.provider,
		"--model",
		resolved.model,
		"--system-prompt",
		resolved.body,
	];
	if (resolved.tools.length > 0) args.push("--tools", resolved.tools.join(","));
	args.push("-p", prompt);

	const childEnv = {
		...process.env,
		TEAMFLOW_AGENT_ROLE: role,
		TEAMFLOW_AGENT_DEPTH: "1",
	};

	let buffer = "";
	let finalText = "";
	let stopReason: string | undefined;
	let errorMessage: string | undefined;
	let wasAborted = false;

	const processLine = (line: string) => {
		const trimmed = line.trim();
		if (!trimmed) return;
		let event: any;
		try {
			event = JSON.parse(trimmed);
		} catch {
			return;
		}
		if (event.type === "message_end" && event.message) {
			const message = event.message;
			if (message.role !== "assistant") return;
			if (message.stopReason) stopReason = message.stopReason;
			if (message.errorMessage) errorMessage = message.errorMessage;
			for (const part of message.content ?? []) {
				if (part.type === "text") finalText = part.text;
			}
		}
	};

	const exitCode = await new Promise<number>((resolve) => {
		const invocation = getPiInvocation(args);
		const proc = spawn(invocation.command, invocation.args, {
			cwd,
			shell: false,
			env: childEnv,
			stdio: ["ignore", "pipe", "pipe"],
		});

		let childClosed = false;
		let killTimer: ReturnType<typeof setTimeout> | undefined;

		const killProc = () => {
			wasAborted = true;
			proc.kill("SIGTERM");
			killTimer = setTimeout(() => {
				if (!childClosed && proc.exitCode === null && proc.signalCode === null) proc.kill("SIGKILL");
			}, 5000);
		};

		const cleanup = () => {
			childClosed = true;
			if (killTimer) clearTimeout(killTimer);
			if (signal) signal.removeEventListener("abort", killProc);
		};

		proc.stdout.on("data", (data) => {
			buffer += data.toString();
			const lines = buffer.split("\n");
			buffer = lines.pop() || "";
			for (const line of lines) processLine(line);
		});
		proc.stderr.on("data", (data) => {
			stderr.text += data.toString();
		});
		proc.on("close", (code) => {
			if (buffer.trim()) processLine(buffer);
			cleanup();
			resolve(code ?? 1);
		});
		proc.on("error", (error) => {
			stderr.text += String(error);
			cleanup();
			resolve(1);
		});

		if (signal) {
			if (signal.aborted) killProc();
			else signal.addEventListener("abort", killProc, { once: true });
		}
	});

	if (wasAborted) {
		return {
			agent: role,
			success: false,
			content: `Task for role "${role}" was aborted by cancellation.`,
			exitCode,
			stopReason,
			stderr: stderr.text,
		};
	}
	if (exitCode !== 0) {
		const tail = stderr.text.trim().split("\n").slice(-5).join("\n");
		return {
			agent: role,
			success: false,
			content: `Role "${role}" exited with nonzero code ${exitCode}.${tail ? `\n${tail}` : ""}`,
			exitCode,
			stopReason,
			stderr: stderr.text,
		};
	}
	if (stopReason === "error" || stopReason === "aborted" || stopReason === "length") {
		return {
			agent: role,
			success: false,
			content: `Role "${role}" stopped with reason "${stopReason}"${errorMessage ? `: ${errorMessage}` : ""}`,
			exitCode,
			stopReason,
			stderr: stderr.text,
		};
	}

	return {
		agent: role,
		success: true,
		content: finalText || "(no output)",
		exitCode,
		stopReason,
		stderr: stderr.text,
	};
}

const TaskParams = Type.Object({
	agent: Type.String({ description: "Teamflow role name; resolved to agents/<role>.md by filename" }),
	prompt: Type.String({ description: "Task prompt handed to the role" }),
});

const TaskGroupParams = Type.Object({
	tasks: Type.Array(
		Type.Object({
			agent: Type.String({ description: "Teamflow role name" }),
			prompt: Type.String({ description: "Task prompt for the role" }),
		}),
	),
	max_concurrency: Type.Optional(
		Type.Number({ description: "Maximum concurrent children (default 3, minimum 1, maximum 8)" }),
	),
});

export default function (pi: ExtensionAPI) {
	// Delegation is an explicit role permission and is available only at depth
	// 0. Children always run at depth 1, so they can never re-register tools
	// even if their role frontmatter also contains `delegates: true`.
	const role = process.env.TEAMFLOW_AGENT_ROLE;
	const depth = Number(process.env.TEAMFLOW_AGENT_DEPTH ?? "0");
	if (!roleMayDelegate(role, depth)) return;

	pi.registerTool({
		name: "task",
		label: "Task",
		description:
			"Delegate a task to a Teamflow role defined in .teamflow/agents/<role>.md. " +
			"The role runs in an isolated pi child process with its own context.",
		parameters: TaskParams,

		async execute(_toolCallId, params, signal, _onUpdate, ctx): Promise<AgentToolResult<TaskDetails>> {
			const agent = params.agent.trim();
			const details: TaskDetails = { agent, exitCode: 1, stderr: "" };
			const fail = (text: string): AgentToolResult<TaskDetails> => ({
				content: [{ type: "text", text }],
				details,
				isError: true,
			});

			const result = await runRoleChild(agent, params.prompt, signal, ctx.cwd);
			details.agent = result.agent;
			details.exitCode = result.exitCode;
			details.stopReason = result.stopReason;
			details.stderr = result.stderr;

			if (!result.success) {
				return fail(result.content);
			}
			return {
				content: [{ type: "text", text: result.content }],
				details,
			};
		},
	});

	pi.registerTool({
		name: "task_group",
		label: "Task Group",
		description:
			"Run multiple independent Teamflow role tasks concurrently with bounded " +
			"concurrency. Each task spawns an isolated pi child like the task tool. " +
			"Results are reported as a JSON array in input order, one entry per task " +
			"with agent, success, content, and exitCode.",
		parameters: TaskGroupParams,

		async execute(_toolCallId, params, signal, _onUpdate, ctx): Promise<AgentToolResult> {
			const tasks = params.tasks;
			const maxConcurrent = Math.min(MAX_CONCURRENCY, Math.max(1, Math.floor(params.max_concurrency ?? 3)));
			const results: Array<{ agent: string; success: boolean; content: string; exitCode: number }> =
				new Array(tasks.length);
			let wasAborted = false;
			const abortListener = () => { wasAborted = true; };
			if (signal) {
				if (signal.aborted) wasAborted = true;
				else signal.addEventListener("abort", abortListener);
			}

			// Bounded worker pool: at most maxConcurrent children run at a time.
			// The shared cursor hands out input indexes so results[i] always
			// preserves the original task order.
			let cursor = 0;
			const worker = async () => {
				while (true) {
					const index = cursor++;
					if (index >= tasks.length) return;
					const task = tasks[index];
					if (signal?.aborted) {
						results[index] = {
							agent: task.agent.trim(),
							success: false,
							content: "Task was aborted by cancellation before it started.",
							exitCode: 1,
						};
						continue;
					}
					const result = await runRoleChild(task.agent, task.prompt, signal, ctx.cwd);
					results[index] = {
						agent: result.agent,
						success: result.success,
						content: result.content,
						exitCode: result.exitCode,
					};
				}
			};

			const workers: Array<Promise<void>> = [];
			for (let i = 0; i < Math.min(maxConcurrent, tasks.length); i++) {
				workers.push(worker());
			}

			try {
				await Promise.all(workers);
			} finally {
				signal?.removeEventListener("abort", abortListener);
				// Orphan prevention: every child registers an abort listener that
				// kills its process, and runRoleChild only resolves after the
				// child closed, so reaching this point means no child is left
				// running. If the signal aborted mid-flight, kill propagation
				// already happened inside runRoleChild.
			}

			if (wasAborted) {
				return {
					content: [
						{
							type: "text",
							text: "task_group was aborted by cancellation; running children were killed.",
						},
					],
					isError: true,
				};
			}

			return {
				content: [{ type: "text", text: JSON.stringify(results, null, 2) }],
			};
		},
	});
}
