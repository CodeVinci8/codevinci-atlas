import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Web Console shell (Master Spec §8.2). Dev-прокси /api → Core.
export default defineConfig({
  plugins: [react()],
  server: {
    host: "127.0.0.1",
    port: 3210,
    proxy: {
      "/api": { target: "http://127.0.0.1:8000", changeOrigin: true },
    },
  },
  build: { outDir: "dist", sourcemap: false },
});
