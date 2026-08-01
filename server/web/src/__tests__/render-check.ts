#!/usr/bin/env bun
/**
 * render-check.ts — standalone behavioral render test for the Svelte Memory app.
 *
 * Usage: bun run src/__tests__/render-check.ts <scenario>
 *
 * Scenarios: xss, list, empty, error, loading, pagination, search, detail
 *
 * Exit 0 on success, 1 on failure (assertion details to stderr).
 * NOT a bun:test file — invoked as a standalone Bun process per scenario.
 * The Python test suite calls this once per scenario via subprocess.
 *
 * Requires the build to have run (web/dist must contain the bundle).
 */

import { Window } from 'happy-dom';
import { readdirSync } from 'node:fs';
import { join } from 'node:path';

// ---------------------------------------------------------------------------
// Locate the built bundle
// ---------------------------------------------------------------------------

const DIST = join(import.meta.dir, '..', '..', 'dist'); // server/web/dist
const assetsDir = join(DIST, 'assets');

const jsFile = readdirSync(assetsDir).find(f => f.endsWith('.js'));
if (!jsFile) {
	console.error(`No JS bundle found in ${assetsDir}. Run "bun run build" first.`);
	process.exit(1);
}
const bundlePath = join(assetsDir, jsFile);

// ---------------------------------------------------------------------------
// Scenario argument
// ---------------------------------------------------------------------------

const scenario = process.argv[2];
const VALID_SCENARIOS = [
	'xss', 'list', 'empty', 'error', 'loading', 'pagination', 'search', 'detail',
] as const;
if (!scenario || !VALID_SCENARIOS.includes(scenario as never)) {
	console.error('Usage: bun run src/__tests__/render-check.ts <scenario>');
	console.error(`Scenarios: ${VALID_SCENARIOS.join(', ')}`);
	process.exit(1);
}

// ---------------------------------------------------------------------------
// Assertion helper
// ---------------------------------------------------------------------------

function assert(condition: boolean, message: string): void {
	if (!condition) {
		console.error(`FAIL [${scenario}]: ${message}`);
		process.exit(1);
	}
}

// ---------------------------------------------------------------------------
// happy-dom setup — copy all needed globals from the Window to globalThis
// ---------------------------------------------------------------------------

const window = new Window();
const document = window.document;

const GLOBAL_KEYS = [
	'window', 'document', 'HTMLElement', 'Node', 'Element', 'Text',
	'Comment', 'DocumentFragment', 'customElements', 'MutationObserver',
	'getComputedStyle', 'SVGElement', 'Event', 'CustomEvent', 'KeyboardEvent',
	'MouseEvent', 'InputEvent', 'Headers', 'Response', 'Request',
	'URL', 'URLSearchParams',
] as const;

for (const key of GLOBAL_KEYS) {
	(globalThis as any)[key] = (window as any)[key];
}
(globalThis as any).requestAnimationFrame =
	window.requestAnimationFrame.bind(window);
(globalThis as any).cancelAnimationFrame =
	window.cancelAnimationFrame.bind(window);

// ---------------------------------------------------------------------------
// Mock fetch — tracks all calls, returns scenario-specific responses
// ---------------------------------------------------------------------------

const fetchCalls: string[] = [];

interface MockResponse {
	ok: boolean;
	status: number;
	json: () => Promise<any>;
	text: () => Promise<string>;
}

function makeJsonResponse(data: any, status = 200): MockResponse {
	return {
		ok: status >= 200 && status < 300,
		status,
		json: async () => data,
		text: async () => JSON.stringify(data),
	};
}

/** Return scenario-specific mock data based on the request URL. */
function getMockResponse(rawUrl: string): MockResponse | Promise<never> {
	const isMemoriesList = rawUrl.includes('/api/memories');
	const isMemoryDetail =
		rawUrl.includes('/api/memory') &&
		rawUrl.includes('permalink') &&
		!isMemoriesList;

	switch (scenario) {
		case 'xss':
			if (isMemoriesList) {
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
			break;

		case 'list':
			if (isMemoriesList) {
				return makeJsonResponse({
					items: [
						{ title: 'First Memory', content: 'Content one', permalink: 'a/first', type: 'entity' },
						{ title: 'Second Memory', content: 'Content two', permalink: 'b/second', type: 'procedure' },
					],
					page: 1, page_size: 12, total: 2, total_pages: 1, query: '',
				});
			}
			break;

		case 'empty':
			if (isMemoriesList) {
				return makeJsonResponse({
					items: [], page: 1, page_size: 12,
					total: 0, total_pages: 1, query: '',
				});
			}
			break;

		case 'error':
			if (isMemoriesList) {
				return makeJsonResponse({ error: 'Internal Server Error' }, 500);
			}
			break;

		case 'loading':
			// Never-resolving promise — simulates a pending request
			return new Promise<never>(() => {});

		case 'pagination':
			if (isMemoriesList) {
				const items = Array.from({ length: 12 }, (_, i) => ({
					title: `Memory ${i + 1}`,
					content: `Content ${i + 1}`,
					permalink: `p/item-${i + 1}`,
					type: i % 2 === 0 ? 'entity' : 'procedure',
				}));
				return makeJsonResponse({
					items, page: 1, page_size: 12,
					total: 36, total_pages: 3, query: '',
				});
			}
			break;

		case 'search':
			if (isMemoriesList) {
				return makeJsonResponse({
					items: [
						{ title: 'Searchable Memory', content: 'Found', permalink: 's/search', type: 'entity' },
					],
					page: 1, page_size: 12, total: 1, total_pages: 1, query: '',
				});
			}
			break;

		case 'detail':
			if (isMemoryDetail) {
				return makeJsonResponse({
					title: 'Detail Title',
					content: 'This is the prose content of the memory note.',
					permalink: 'test/detail-note',
					type: 'entity',
				});
			}
			break;
	}

	// Default fallback: empty 200 response for unrecognised URLs
	return makeJsonResponse({});
}

function mockFetch(input: any): Promise<MockResponse> {
	const url = typeof input === 'string'
		? input
		: input?.url ?? String(input);
	fetchCalls.push(url);
	return Promise.resolve(getMockResponse(url));
}

(globalThis as any).fetch = mockFetch;

// ---------------------------------------------------------------------------
// Set location and mount the app
// ---------------------------------------------------------------------------

if (scenario === 'detail') {
	window.location.href = 'http://localhost/memory?permalink=test/detail-note';
} else {
	window.location.href = 'http://localhost/';
}

document.body.innerHTML = '<div id="app"></div>';

// Import the built bundle — this mounts the Svelte app onto #app
await import(bundlePath);

// Wait for rendering (scenario-dependent)
if (scenario === 'loading') {
	// Very short wait — the promise never resolves, so we check the DOM
	// while the loading indicator is visible
	await new Promise(r => setTimeout(r, 50));
} else {
	await new Promise(r => setTimeout(r, 200));
	try {
		await window.happyDOM.whenAsyncComplete();
	} catch {
		// Swallow — some scenarios intentionally leave pending promises
	}
}

// ---------------------------------------------------------------------------
// Scenario assertions
// ---------------------------------------------------------------------------

function bodyText(): string {
	return document.body.textContent || '';
}

switch (scenario) {
	case 'xss': {
		const text = bodyText();
		assert(
			text.includes('<script>alert(1)</script>'),
			'XSS payload title must appear as literal visible text, not executed',
		);
		assert(
			text.includes('<img src=x onerror=alert(1)>'),
			'XSS payload content must appear as literal visible text',
		);
		assert(
			document.querySelectorAll('#app script').length === 0,
			'No <script> elements should be created from XSS payload',
		);
		assert(
			document.querySelectorAll('#app img[onerror]').length === 0,
			'No <img> elements with onerror should be created from XSS payload',
		);
		assert(
			!text.includes('test/xss-note'),
			'Permalink must not appear as visible text content',
		);
		break;
	}

	case 'list': {
		const text = bodyText();
		assert(text.includes('First Memory'), 'First title must appear in text content');
		assert(text.includes('Second Memory'), 'Second title must appear in text content');
		// Type indicator: an element whose trimmed text is exactly the type
		let hasTypeBadge = false;
		for (const el of document.querySelectorAll('#app *')) {
			const t = (el.textContent || '').trim();
			if (t === 'entity' || t === 'procedure') {
				hasTypeBadge = true;
				break;
			}
		}
		assert(hasTypeBadge, 'A type indicator element must exist (entity/procedure)');
		// Detail link: <a> with href containing /memory and permalink=
		let hasDetailLink = false;
		for (const a of document.querySelectorAll('#app a')) {
			const href = a.getAttribute('href') || '';
			if (href.includes('/memory') && href.includes('permalink=')) {
				hasDetailLink = true;
				break;
			}
		}
		assert(hasDetailLink, 'At least one <a> must link to /memory?permalink=...');
		// Permalinks not visible as text
		assert(!text.includes('a/first'), 'Permalink a/first must not be visible text');
		assert(!text.includes('b/second'), 'Permalink b/second must not be visible text');
		break;
	}

	case 'empty': {
		const text = bodyText().toLowerCase();
		assert(
			text.includes('no memories') || text.includes('nothing') || text.includes('empty'),
			'Empty state must show a message like "No memories", "nothing", or "empty"',
		);
		break;
	}

	case 'error': {
		const text = bodyText().toLowerCase();
		assert(
			text.includes('failed') || text.includes('error') || text.includes('try again'),
			'Error state must show a message like "Failed", "error", or "try again"',
		);
		break;
	}

	case 'loading': {
		const text = bodyText().toLowerCase();
		assert(
			text.includes('loading'),
			'Loading state must show a loading indicator containing "Loading"',
		);
		break;
	}

	case 'pagination': {
		const text = bodyText();
		assert(text.includes('1'), 'Page indicator must show current page "1"');
		assert(text.includes('3'), 'Page indicator must show total pages "3"');
		// Previous button must be disabled on page 1
		const disabledButtons = document.querySelectorAll('button[disabled]');
		let hasDisabledPrev = false;
		for (const btn of disabledButtons) {
			const t = (btn.textContent || '').toLowerCase();
			if (t.includes('previous') || t.includes('prev')) {
				hasDisabledPrev = true;
				break;
			}
		}
		assert(hasDisabledPrev, 'Previous button must exist and be disabled on page 1');
		// Next button must exist and be enabled
		let nextBtn: any = null;
		for (const btn of document.querySelectorAll('#app button')) {
			const t = (btn.textContent || '').toLowerCase();
			if (t.includes('next') && !btn.hasAttribute('disabled')) {
				nextBtn = btn;
				break;
			}
		}
		assert(nextBtn !== null, 'Next button must exist and not be disabled');
		// Click Next → triggers fetch with page=2
		fetchCalls.length = 0; // reset to track the click-triggered call
		nextBtn.dispatchEvent(new window.MouseEvent('click', { bubbles: true }));
		await new Promise(r => setTimeout(r, 200));
		try { await window.happyDOM.whenAsyncComplete(); } catch {}
		assert(
			fetchCalls.some(u => u.includes('page=2')),
			'Clicking Next must trigger a fetch with page=2',
		);
		break;
	}

	case 'search': {
		// Find search input
		const searchInput =
			document.querySelector('#app input[type="search"]') ||
			document.querySelector('#app input[name="query"]');
		assert(searchInput !== null, 'A search input must exist');
		// Set value and dispatch input event
		(searchInput as any).value = 'test-query';
		searchInput!.dispatchEvent(new window.InputEvent('input', { bubbles: true }));
		// Find and submit the form
		const form = document.querySelector('#app form');
		assert(form !== null, 'A search form must exist');
		fetchCalls.length = 0; // reset to track the submit-triggered call
		form!.dispatchEvent(new window.Event('submit', { bubbles: true, cancelable: true }));
		await new Promise(r => setTimeout(r, 200));
		try { await window.happyDOM.whenAsyncComplete(); } catch {}
		assert(
			fetchCalls.some(u => u.includes('query=test-query')),
			'Search submission must trigger fetch with query=test-query',
		);
		assert(
			fetchCalls.some(u => u.includes('page=1')),
			'Search must reset to page=1',
		);
		break;
	}

	case 'detail': {
		const text = bodyText();
		assert(text.includes('Detail Title'), 'Detail title must appear in text content');
		assert(
			text.includes('This is the prose content'),
			'Detail content prose must be rendered',
		);
		// Back link: <a> navigating to list
		let hasBackLink = false;
		for (const a of document.querySelectorAll('#app a')) {
			const href = (a.getAttribute('href') || '').toLowerCase();
			const linkText = (a.textContent || '').toLowerCase();
			if (
				href === '/' || href === '/app' || href === '/app/' ||
				href.includes('back') || linkText.includes('back')
			) {
				hasBackLink = true;
				break;
			}
		}
		assert(hasBackLink, 'A back link must exist (href of / or /app, or text "Back")');
		// Permalink not visible
		assert(
			!text.includes('test/detail-note'),
			'Permalink test/detail-note must not appear as visible text',
		);
		break;
	}
}

console.error(`PASS [${scenario}]`);
process.exit(0);
