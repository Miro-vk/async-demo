import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    // The API runs separately; proxying keeps the front end origin-relative so
    // there is nothing to configure when it is served from the same host.
    proxy: { "/api": "http://127.0.0.1:8000" },
  },
});
