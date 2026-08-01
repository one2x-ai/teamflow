import "./app.css";
import { mount } from "svelte";
import App from "./App.svelte";

// Svelte's event helper evaluates `target instanceof HTMLMediaElement` when
// registering listeners. Headless DOM shims (e.g. happy-dom in the render
// checks) don't define that global, so provide a harmless stand-in; real
// browsers already have it and skip this branch.
if (typeof globalThis.HTMLMediaElement === "undefined") {
  (globalThis as Record<string, unknown>).HTMLMediaElement = class {};
}

const app = mount(App, {
  target: document.getElementById("app")!,
});

export default app;
