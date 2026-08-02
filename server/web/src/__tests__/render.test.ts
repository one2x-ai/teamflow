import { test, expect } from "bun:test";
import { Window } from "happy-dom";
import { readdirSync } from "node:fs";
import { join } from "node:path";

// Strip proxy env vars — if present they break fetch in the test process
for (const key of ["HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"]) {
	delete process.env[key];
}

// ---------------------------------------------------------------------------
// Locate the built bundle
// ---------------------------------------------------------------------------

const DIST = join(import.meta.dir, "..", "..", "dist");
const assetsDir = join(DIST, "assets");

function findBundle(): string {
	const jsFile = readdirSync(assetsDir).find(f => f.endsWith(".js"));
	if (!jsFile) throw new Error("No JS bundle. Run bun run build first.");
	return join(assetsDir, jsFile);
}

// ---------------------------------------------------------------------------
// happy-dom globals to copy onto globalThis for each scenario
// ---------------------------------------------------------------------------

const GLOBAL_KEYS = [
	"window", "document", "HTMLElement", "Node", "Element", "Text",
	"Comment", "DocumentFragment", "customElements", "MutationObserver",
	"getComputedStyle", "SVGElement", "Event", "CustomEvent", "KeyboardEvent",
	"MouseEvent", "InputEvent", "Headers", "Response", "Request",
	"URL", "URLSearchParams",
] as const;

// ---------------------------------------------------------------------------
// Mock response helper
// ---------------------------------------------------------------------------

function makeJsonResponse(data: any, status = 200) {
	return {
		ok: status >= 200 && status < 300,
		status,
		json: async () => data,
		text: async () => JSON.stringify(data),
	};
}

// ---------------------------------------------------------------------------
// Render result
// ---------------------------------------------------------------------------

interface RenderResult {
	window: Window;
	document: Document;
	bodyText: () => string;
	fetchCalls: string[];
}

/**
 * Set up a fresh happy-dom environment, mock fetch, and import the built
 * bundle with a cache-busting query so each test gets a fresh module
 * evaluation.
 */
async function renderScenario(
	scenario: string,
	mockFetch: (url: string) => any,
	location: string,
	waitMs: number,
	skipAsyncWait = false,
): Promise<RenderResult> {
	const window = new Window();
	const document = window.document;
	const fetchCalls: string[] = [];

	// Copy globals from happy-dom Window to globalThis
	for (const key of GLOBAL_KEYS) {
		(globalThis as any)[key] = (window as any)[key];
	}
	(globalThis as any).requestAnimationFrame = window.requestAnimationFrame.bind(window);
	(globalThis as any).cancelAnimationFrame = window.cancelAnimationFrame.bind(window);

	// Mock fetch
	(globalThis as any).fetch = (input: any) => {
		const url = typeof input === "string" ? input : input?.url ?? String(input);
		fetchCalls.push(url);
		const response = mockFetch(url);
		return Promise.resolve(response);
	};

	// Set location
	window.location.href = location;

	// Mount point
	document.body.innerHTML = '<div id="app"></div>';

	// Import bundle with cache-busting for fresh module evaluation
	const bundlePath = findBundle();
	await import(bundlePath + "?t=" + scenario + Date.now());

	// Wait for render
	await new Promise(r => setTimeout(r, waitMs));
	if (!skipAsyncWait) {
		try {
			await window.happyDOM.whenAsyncComplete();
		} catch {
			// Swallow — some scenarios intentionally leave pending promises
		}
	}

	return {
		window,
		document,
		bodyText: () => document.body.textContent || "",
		fetchCalls,
	};
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

test("xss", async () => {
	const { document, bodyText } = await renderScenario(
		"xss",
		(url) => {
			if (url.includes("/api/memories")) {
				return makeJsonResponse({
					items: [{
						title: '<script>alert(1)</script>',
						content: '<img src=x onerror=alert(1)>',
						permalink: 'test/xss-note',
						type: 'entity',
					}],
					page: 1, page_size: 12, total: 1, total_pages: 1, query: '',
				});
			}
			return makeJsonResponse({});
		},
		"http://localhost/",
		200,
	);

	const text = bodyText();
	expect(text).toContain('<script>alert(1)</script>');
	expect(text).toContain('<img src=x onerror=alert(1)>');
	expect(document.querySelectorAll("#app script").length).toBe(0);
	expect(document.querySelectorAll("#app img[onerror]").length).toBe(0);
	expect(text).not.toContain("test/xss-note");
});

test("list", async () => {
	const { document, bodyText } = await renderScenario(
		"list",
		(url) => {
			if (url.includes("/api/memories")) {
				return makeJsonResponse({
					items: [
						{ title: "First Memory", content: "Content one", permalink: "a/first", type: "entity" },
						{ title: "Second Memory", content: "Content two", permalink: "b/second", type: "procedure" },
					],
					page: 1, page_size: 12, total: 2, total_pages: 1, query: '',
				});
			}
			return makeJsonResponse({});
		},
		"http://localhost/",
		200,
	);

	const text = bodyText();
	expect(text).toContain("First Memory");
	expect(text).toContain("Second Memory");

	// Type indicator: an element whose trimmed text is exactly the type
	let hasTypeBadge = false;
	for (const el of document.querySelectorAll("#app *")) {
		const t = (el.textContent || "").trim();
		if (t === "entity" || t === "procedure") {
			hasTypeBadge = true;
			break;
		}
	}
	expect(hasTypeBadge).toBe(true);

	// Detail link: <a> with href containing /memory and permalink=
	let hasDetailLink = false;
	for (const a of document.querySelectorAll("#app a")) {
		const href = a.getAttribute("href") || "";
		if (href.includes("/memory") && href.includes("permalink=")) {
			hasDetailLink = true;
			break;
		}
	}
	expect(hasDetailLink).toBe(true);

	// Permalinks not visible as text
	expect(text).not.toContain("a/first");
	expect(text).not.toContain("b/second");
});

test("empty", async () => {
	const { bodyText } = await renderScenario(
		"empty",
		(url) => {
			if (url.includes("/api/memories")) {
				return makeJsonResponse({
					items: [], page: 1, page_size: 12,
					total: 0, total_pages: 1, query: '',
				});
			}
			return makeJsonResponse({});
		},
		"http://localhost/",
		200,
	);

	const text = bodyText().toLowerCase();
	expect(
		text.includes("no memories") || text.includes("nothing") || text.includes("empty"),
	).toBe(true);
});

test("error", async () => {
	const { bodyText } = await renderScenario(
		"error",
		(url) => {
			if (url.includes("/api/memories")) {
				return makeJsonResponse({ error: "Internal Server Error" }, 500);
			}
			return makeJsonResponse({});
		},
		"http://localhost/",
		200,
	);

	const text = bodyText().toLowerCase();
	expect(
		text.includes("failed") || text.includes("error") || text.includes("try again"),
	).toBe(true);
});

test("loading", async () => {
	const { bodyText } = await renderScenario(
		"loading",
		() => new Promise<never>(() => {}),
		"http://localhost/",
		50,
		true,
	);

	const text = bodyText().toLowerCase();
	expect(text).toContain("loading");
});

test("pagination", async () => {
	const { window, document, bodyText, fetchCalls } = await renderScenario(
		"pagination",
		(url) => {
			if (url.includes("/api/memories")) {
				const items = Array.from({ length: 12 }, (_, i) => ({
					title: `Memory ${i + 1}`,
					content: `Content ${i + 1}`,
					permalink: `p/item-${i + 1}`,
					type: i % 2 === 0 ? "entity" : "procedure",
				}));
				return makeJsonResponse({
					items, page: 1, page_size: 12,
					total: 36, total_pages: 3, query: '',
				});
			}
			return makeJsonResponse({});
		},
		"http://localhost/",
		200,
	);

	const text = bodyText();
	expect(text).toContain("1");
	expect(text).toContain("3");

	// Previous button must be disabled on page 1
	const disabledButtons = document.querySelectorAll("button[disabled]");
	let hasDisabledPrev = false;
	for (const btn of disabledButtons) {
		const t = (btn.textContent || "").toLowerCase();
		if (t.includes("previous") || t.includes("prev")) {
			hasDisabledPrev = true;
			break;
		}
	}
	expect(hasDisabledPrev).toBe(true);

	// Next button must exist and be enabled
	let nextBtn: any = null;
	for (const btn of document.querySelectorAll("#app button")) {
		const t = (btn.textContent || "").toLowerCase();
		if (t.includes("next") && !btn.hasAttribute("disabled")) {
			nextBtn = btn;
			break;
		}
	}
	expect(nextBtn).not.toBeNull();

	// Click Next → triggers fetch with page=2
	fetchCalls.length = 0;
	nextBtn.dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
	await new Promise(r => setTimeout(r, 200));
	try { await window.happyDOM.whenAsyncComplete(); } catch {}

	expect(fetchCalls.some(u => u.includes("page=2"))).toBe(true);
});

test("search", async () => {
	const { window, document, fetchCalls } = await renderScenario(
		"search",
		(url) => {
			if (url.includes("/api/memories")) {
				return makeJsonResponse({
					items: [
						{ title: "Searchable Memory", content: "Found", permalink: "s/search", type: "entity" },
					],
					page: 1, page_size: 12, total: 1, total_pages: 1, query: '',
				});
			}
			return makeJsonResponse({});
		},
		"http://localhost/",
		200,
	);

	// Find search input
	const searchInput =
		document.querySelector('#app input[type="search"]') ||
		document.querySelector('#app input[name="query"]');
	expect(searchInput).not.toBeNull();

	// Set value and dispatch input event
	(searchInput as any).value = "test-query";
	searchInput!.dispatchEvent(new window.InputEvent("input", { bubbles: true }));

	// Find and submit the form
	const form = document.querySelector("#app form");
	expect(form).not.toBeNull();

	fetchCalls.length = 0;
	form!.dispatchEvent(new window.Event("submit", { bubbles: true, cancelable: true }));
	await new Promise(r => setTimeout(r, 200));
	try { await window.happyDOM.whenAsyncComplete(); } catch {}

	expect(fetchCalls.some(u => u.includes("query=test-query"))).toBe(true);
	expect(fetchCalls.some(u => u.includes("page=1"))).toBe(true);
});

test("detail", async () => {
	const { document, bodyText } = await renderScenario(
		"detail",
		(url) => {
			const isMemoryDetail =
				url.includes("/api/memory") &&
				url.includes("permalink") &&
				!url.includes("/api/memories");
			if (isMemoryDetail) {
				return makeJsonResponse({
					title: "Detail Title",
					content: "This is the prose content of the memory note.",
					permalink: "test/detail-note",
					type: "entity",
				});
			}
			return makeJsonResponse({});
		},
		"http://localhost/memory?permalink=test/detail-note",
		200,
	);

	const text = bodyText();
	expect(text).toContain("Detail Title");
	expect(text).toContain("This is the prose content");

	// Back link: <a> navigating to list
	let hasBackLink = false;
	for (const a of document.querySelectorAll("#app a")) {
		const href = (a.getAttribute("href") || "").toLowerCase();
		const linkText = (a.textContent || "").toLowerCase();
		if (
			href === "/" || href === "/app" || href === "/app/" ||
			href.includes("back") || linkText.includes("back")
		) {
			hasBackLink = true;
			break;
		}
	}
	expect(hasBackLink).toBe(true);

	// Permalink not visible
	expect(text).not.toContain("test/detail-note");
});
