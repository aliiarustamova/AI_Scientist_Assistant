import { defineConfig, loadEnv } from "vite";
import react from "@vitejs/plugin-react-swc";
import path from "path";
import { componentTagger } from "lovable-tagger";

// https://vitejs.dev/config/
export default defineConfig(({ mode }) => {
  // Repo root .env: set VITE_DEV_BACKEND to override the Flask URL the vite
  // proxy targets (e.g. http://127.0.0.1:8000 when running Flask under a
  // non-default port). Default matches `python app.py` with PORT unset
  // (Flask binds to 5000) — the path of least surprise.
  const env = loadEnv(mode, path.resolve(__dirname, ".."), "");
  const devBackend =
    (env.VITE_DEV_BACKEND && env.VITE_DEV_BACKEND.trim()) ||
    "http://127.0.0.1:5000";

  return {
  server: {
    host: "::",
    port: 8080,
    hmr: {
      overlay: false,
    },
    // Dev-only proxy so the SPA can call /lit-review, /protocol, /materials
    // as if they were same-origin. In production these are expected to live
    // behind a reverse proxy or under the same origin as the Flask app.
    proxy: {
      "/lit-review":          devBackend,
      "/protocol":            devBackend,
      "/protocol/pdf":        devBackend,
      "/protocol-candidates": devBackend,
      "/materials":           devBackend,
      "/timeline":            devBackend,
      "/validation":          devBackend,
      "/critique":            devBackend,
      "/chat":                devBackend,
      "/health":              devBackend,
    },
  },
  plugins: [react(), mode === "development" && componentTagger()].filter(Boolean),
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
    },
    dedupe: ["react", "react-dom", "react/jsx-runtime", "react/jsx-dev-runtime", "@tanstack/react-query", "@tanstack/query-core"],
  },
  };
});
