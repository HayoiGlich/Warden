import path from "node:path";
import { defineConfig, loadEnv } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig(({ mode }) => {
  const rootDir = path.resolve(__dirname, "..");
  const env = loadEnv(mode, rootDir, "");
  const backendHost = env.APP_HOST || "127.0.0.1";
  const backendPort = env.APP_PORT || "8000";

  // 0.0.0.0 is valid for server bind, but not as an outbound proxy destination.
  const proxyHost = backendHost === "0.0.0.0" ? "127.0.0.1" : backendHost;
  const proxyTarget = `http://${proxyHost}:${backendPort}`;

  return {
    envDir: "..",
    plugins: [react()],
    server: {
      host: "0.0.0.0",
      port: 5173,
      proxy: {
        "/api": proxyTarget
      }
    },
    preview: {
      host: "0.0.0.0",
      port: 4173
    }
  };
});
