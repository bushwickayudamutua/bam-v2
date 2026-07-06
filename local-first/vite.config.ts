import { defineConfig } from "vite";
import wasm from "vite-plugin-wasm";

export default defineConfig({
  root: "web",
  plugins: [wasm()],
  build: {
    target: "esnext",
    outDir: "../dist",
    emptyOutDir: true,
  },
  optimizeDeps: {
    // The subduction WASM package auto-initializes on import; pre-bundling
    // breaks its asset URL resolution.
    exclude: ["@automerge/automerge-subduction", "@automerge/automerge"],
  },
});
