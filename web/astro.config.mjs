import { defineConfig } from "astro/config";

export default defineConfig({
  outDir: "./dist",
  devToolbar: { enabled: false },
  vite: {
    server: {
      proxy: {
        "/api": {
          target: "http://127.0.0.1:8765",
          changeOrigin: true,
        },
      },
    },
  },
});
