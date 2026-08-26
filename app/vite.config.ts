import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// The built SPA is served by FastAPI from app/frontend/dist, so `root` points at the
// frontend directory while the build output stays alongside it.
export default defineConfig({
  root: "frontend",
  plugins: [react()],
  build: {
    outDir: "dist",
    emptyOutDir: true,
    // Databricks Apps rejects any single file over 10 MB. Splitting the chart library
    // out keeps every chunk comfortably under that.
    rollupOptions: {
      output: {
        manualChunks: {
          react: ["react", "react-dom"],
          charts: ["recharts"],
        },
      },
    },
  },
  server: {
    port: 5173,
    proxy: { "/api": "http://127.0.0.1:8000" },
  },
});
