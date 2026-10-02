import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// The API the dev server proxies.
const apiTarget = process.env.VITE_PROXY_TARGET || "http://localhost:8000";

export default defineConfig({
  plugins: [react()],
  server: {
    port: Number(process.env.PORT) || 5173,
    proxy: {
      // Dev server proxies the API so the browser sees a single origin and CORS stays out of the way.
      "/api": {
        target: apiTarget,
        changeOrigin: true,
        rewrite: (p) => p.replace(/^\/api/, ""),
      },
    },
  },
});
