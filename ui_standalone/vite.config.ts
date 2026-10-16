import { defineConfig } from "vite";

// Dev: proxy API to the local chat_engine. Build: output served by chat_engine at "/".
export default defineConfig({
  base: "/",
  build: { outDir: "dist", emptyOutDir: true, target: "es2022" },
  server: {
    port: 5173,
    proxy: { "/v1": "http://localhost:8080", "/healthz": "http://localhost:8080", "/openapi.json": "http://localhost:8080" },
  },
});
