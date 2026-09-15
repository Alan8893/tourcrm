/// <reference types="vitest/config" />
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// TH-0088: the frontend now makes real API calls (see src/api/client.ts).
// The backend has no CORS configuration (application-architecture.md's
// topology puts a reverse proxy in front of both apps in every real
// deployment mode), so the dev server proxies `/api` itself instead —
// a frontend-only, tooling-level change, not a backend change. Defaults
// to `http://localhost:8000` for `npm run dev` on the host; Docker
// Compose overrides this to the `backend` service hostname (see
// docker-compose.yml's `frontend.environment`).
const apiProxyTarget = process.env.VITE_API_PROXY_TARGET ?? "http://localhost:8000";

export default defineConfig({
  plugins: [react()],
  server: {
    // Bind to all interfaces so the dev server is reachable from outside
    // its Docker container (Issue #8); harmless for bare `npm run dev` too.
    host: true,
    port: 5173,
    proxy: {
      "/api": {
        target: apiProxyTarget,
        changeOrigin: true,
      },
    },
  },
  test: {
    environment: "jsdom",
    setupFiles: ["./src/test/setup.ts"],
    globals: false,
  },
});
