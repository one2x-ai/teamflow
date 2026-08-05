/**
 * Liveness ignition for one teamflow agent process.
 *
 * Two mechanical acts on the first agent start, then nothing:
 *
 * 1. Mark this process's handoff `running`. The receiver owns that
 *    transition, and hanging it on a lifecycle hook means no model has to
 *    remember it.
 * 2. Spawn a detached watchdog for this pid. A plugin dies inside the
 *    process it would report on, so the exit receipt has to come from
 *    outside; `detached` + ignored stdio + `unref()` is what lets the
 *    monitor outlive its subject without holding this event loop open.
 *
 * No tools are registered: this extension has no model-facing surface. With
 * no run-id in the environment there is no run to record, so it does
 * nothing rather than failing — running `pi` by hand stays possible.
 */

import { spawn, spawnSync } from "node:child_process";
import * as fs from "node:fs";
import * as path from "node:path";
import { fileURLToPath } from "node:url";
import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";

// .teamflow/extensions/agent-watchdog/index.ts -> .teamflow
const RUNTIME_DIR = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..", "..");
const WATCHDOG_SCRIPT = path.join(
	RUNTIME_DIR,
	"skills",
	"observe-inner-loop",
	"scripts",
	"watchdog.py",
);
const HANDOFF_CLI = path.join(
	RUNTIME_DIR,
	"skills",
	"write-handoff",
	"scripts",
	"handoff_state.py",
);
const HEARTBEAT_SECONDS = "60";

let ignited = false;

function markHandoffRunning(runId: string, handoffId: string): void {
	if (!fs.existsSync(HANDOFF_CLI)) return;
	spawnSync(
		"python3",
		[
			HANDOFF_CLI,
			"handoff",
			"start",
			"--run-id",
			runId,
			"--id",
			handoffId,
			"--pid",
			String(process.pid),
		],
		{ stdio: "ignore" },
	);
}

function igniteWatchdog(runId: string, role: string, depth: string): void {
	if (!fs.existsSync(WATCHDOG_SCRIPT)) return;
	const child = spawn(
		"python3",
		[
			WATCHDOG_SCRIPT,
			"--pid",
			String(process.pid),
			"--role",
			role,
			"--depth",
			depth,
			"--run-id",
			runId,
			"--interval",
			HEARTBEAT_SECONDS,
		],
		{ detached: true, stdio: "ignore" },
	);
	child.unref();
}

export default function (pi: ExtensionAPI) {
	pi.on("before_agent_start", () => {
		if (ignited) return;
		ignited = true;

		const runId = process.env.TEAMFLOW_RUN_ID;
		if (!runId) return;
		const role = process.env.TEAMFLOW_AGENT_ROLE ?? "unknown";
		const depth = process.env.TEAMFLOW_AGENT_DEPTH ?? "0";
		const handoffId = process.env.TEAMFLOW_HANDOFF_ID;

		if (handoffId) markHandoffRunning(runId, handoffId);
		igniteWatchdog(runId, role, depth);
	});
}
