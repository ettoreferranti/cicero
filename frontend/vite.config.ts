/// <reference types="vitest/config" />
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// The dev server proxies API calls to the backend so the app can use
// same-origin relative URLs (and EventSource works without CORS).
// Keep this list in step with the nginx proxy in frontend/nginx.conf, which
// does the same job for the built image.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/chambers": "http://127.0.0.1:8000",
      "/providers": "http://127.0.0.1:8000",
      "/health": "http://127.0.0.1:8000",
      // Without this the UI's capability probe hits the dev server's SPA
      // fallback, so "web research disabled on this server" never showed.
      "/config": "http://127.0.0.1:8000",
    },
  },
  test: {
    globals: true,
    environment: "jsdom",
    setupFiles: ["./vitest.setup.ts"],
    css: false,
  },
});
