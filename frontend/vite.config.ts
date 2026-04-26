import { defineConfig } from "vite";
import react from "@vitejs/plugin-react-swc";
import path from "path";
import { componentTagger } from "lovable-tagger";

// https://vitejs.dev/config/
export default defineConfig(({ mode }) => {
  // Match local Flask default when running `PORT=8000 python3 app.py`.
  // Can be overridden per-machine with VITE_API_PROXY_TARGET.
  // Example: VITE_API_PROXY_TARGET=http://localhost:5000 npm run dev
  const apiProxyTarget =
    process.env.VITE_API_PROXY_TARGET ?? "http://localhost:8000";

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
        "/parse-hypothesis": apiProxyTarget,
        "/lit-review": apiProxyTarget,
        "/protocol-sources": apiProxyTarget,
        "/protocol": apiProxyTarget,
        "/materials": apiProxyTarget,
        "/health": apiProxyTarget,
      },
    },
    plugins: [react(), mode === "development" && componentTagger()].filter(
      Boolean
    ),
    resolve: {
      alias: {
        "@": path.resolve(__dirname, "./src"),
      },
      dedupe: [
        "react",
        "react-dom",
        "react/jsx-runtime",
        "react/jsx-dev-runtime",
        "@tanstack/react-query",
        "@tanstack/query-core",
      ],
    },
  };
});
