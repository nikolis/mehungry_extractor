import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Served by FastAPI from /app in production; "base" makes the built asset URLs resolve there.
// In dev, Vite serves at the root and proxies API calls to the running mehungry-api.
export default defineConfig(({ command }) => ({
  base: command === "build" ? "/app/" : "/",
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/observe": "http://127.0.0.1:8000",
      "/analyze": "http://127.0.0.1:8000",
      "/health": "http://127.0.0.1:8000",
    },
  },
}));
