import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import path from "path";
import { defineConfig, type Plugin } from "vite";

/**
 * Strip `crossorigin` attributes from built HTML.
 * Electron serves from file:// protocol where crossorigin triggers
 * CORS failures because there is no server to respond with headers.
 */
function stripCrossOrigin(): Plugin {
  return {
    name: "strip-crossorigin",
    transformIndexHtml(html) {
      return html.replace(/\s+crossorigin/g, "");
    },
  };
}

export default defineConfig({
  base: "./",
  plugins: [react(), tailwindcss(), stripCrossOrigin()],
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
    },
  },
  server: {
    port: 5173,
    proxy: {
      "/api": {
        target: "http://localhost:8000",
        changeOrigin: true,
      },
    },
  },
});