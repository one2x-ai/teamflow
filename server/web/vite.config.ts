import path from "node:path";
import { svelte } from "@sveltejs/vite-plugin-svelte";
import tailwindcss from "@tailwindcss/vite";
import { defineConfig } from "vite";

export default defineConfig({
  // The app is served under /app, so generated asset URLs must carry that
  // prefix. Without it index.html points at /assets/* while static.ts only
  // serves /app/assets/*, and every script and stylesheet 404s in the
  // browser even though the files exist.
  base: "/app/",
  plugins: [tailwindcss(), svelte()],
  resolve: {
    alias: {
      $lib: path.resolve("./src/lib"),
    },
  },
  build: {
    outDir: "dist",
    rollupOptions: {
      output: {
        // Hex (not base64) content hashes in asset filenames, so hashed
        // assets are trivially recognisable as content-addressed files.
        hashCharacters: "hex",
      },
    },
  },
});
