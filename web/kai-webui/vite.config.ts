import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    host: "0.0.0.0",
    port: 3501,
    proxy: {
      "/api": "http://172.20.109.174:8800",
    },
  },
});
