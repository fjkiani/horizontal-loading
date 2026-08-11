import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// The backend sleeps on Render's free tier. Everything that can be static IS
// static (public/catalog.json is baked at build time from generated_pool.json),
// so the console is fully browsable with the API cold. Only live generation
// needs the origin, and the client warms it on first paint.
export default defineConfig({
  plugins: [react()],
  build: { outDir: "dist", sourcemap: false },
  server: {
    proxy: {
      "/api": {
        target: process.env.SEAL_API || "http://127.0.0.1:8770",
        changeOrigin: true,
      },
    },
  },
});
