import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// Em desenvolvimento a API fica no mesmo domínio via proxy; assim o backend não precisa de CORS.
const proxy = { "/api": process.env.MP_API_URL ?? "http://127.0.0.1:8010" };

export default defineConfig({
  plugins: [react()],
  server: { proxy },
  preview: { proxy },
});
