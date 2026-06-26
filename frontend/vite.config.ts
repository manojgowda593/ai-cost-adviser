import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";

const __dirname = dirname(fileURLToPath(import.meta.url));

// Read the centralized .env from the project root (one dir up from frontend/),
// so backend and frontend share a single env file. Only VITE_-prefixed vars
// are exposed to the browser.
export default defineConfig({
  plugins: [react()],
  server: { port: 5173 },
  envDir: resolve(__dirname, ".."),
});
