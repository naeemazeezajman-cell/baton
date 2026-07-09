import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

// `npm run build`       → production app (base "/", VITE_DEMO_MODE=false)
// `npm run build:demo`  → in-memory demo for GitHub Pages (base "/baton/", VITE_DEMO_MODE=true)
export default defineConfig(({ mode }) => ({
  base: mode === "demo" ? "/baton/" : "/",
  plugins: [react(), tailwindcss()],
}));
