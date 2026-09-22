import { defineConfig } from "vite";

export default defineConfig({
  server: {
    port: 5173,
    strictPort: true,
    // No rewrite: the API lives under /api in every environment, so a DPoP
    // proof signed in dev binds the same path it will bind in production.
    proxy: { "/api": { target: "http://127.0.0.1:8000" } },
  },
});
