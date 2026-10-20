import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Em desenvolvimento (npm run dev), a API é o servidor Python: python -m cipher_studio web
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: { "/api": "http://127.0.0.1:8000" },
  },
});
