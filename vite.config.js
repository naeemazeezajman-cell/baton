import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

// `npm run build`            → production app for custom-domain hosting (base "/", VITE_DEMO_MODE=false)
// `npm run build:demo`       → in-memory demo for GitHub Pages (base "/baton/", VITE_DEMO_MODE=true)
// `npm run build:prod-pages` → API-backed production app on GitHub Pages under /baton/app/
export default defineConfig(({ mode }) => ({
  base: mode === "demo" ? "/baton/" : mode === "prod-pages" ? "/baton/app/" : "/",
  plugins: [react(), tailwindcss()],
}));
