import { defineConfig, loadEnv } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), "");
  const apiKey = env.VITE_API_KEY || "";

  return {
    plugins: [react(), tailwindcss()],
    server: {
      proxy: {
        "/api": {
          target: env.VITE_DEV_API_PROXY_TARGET || "http://localhost:8080",
          changeOrigin: true,
          headers: apiKey ? { "X-API-Key": apiKey } : undefined,
        },
      },
    },
    test: {
      environment: "jsdom",
      setupFiles: ["./src/test/setup.ts"],
      globals: true,
      include: ["src/**/*.{test,spec}.{ts,tsx}"],
      exclude: ["node_modules/**"],
    },
  };
});
