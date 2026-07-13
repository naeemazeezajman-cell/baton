import { StrictMode, Suspense, lazy } from "react";
import { createRoot } from "react-dom/client";
import "./index.css";

// VITE_DEMO_MODE=true → the self-contained in-memory demo (the public portfolio site).
// Production build → the API-backed app (real login, server state).
// A "platform" path segment → the Platform Operator console (the developer's own login
// above all tenants). Segment match instead of a prefix check so it works at /platform on
// a root-domain deploy AND /baton/app/platform under the GitHub Pages base path — including
// when Pages serves the route via the 404.html SPA fallback.
const DEMO = import.meta.env.VITE_DEMO_MODE === "true";
const PLATFORM = window.location.pathname.split("/").includes("platform");
const App = lazy(() =>
  PLATFORM ? import("./platform/index.jsx")
    : DEMO ? import("./baton-prototype.jsx") : import("./app-production.jsx"));

createRoot(document.getElementById("root")).render(
  <StrictMode>
    <Suspense fallback={null}>
      <App />
    </Suspense>
  </StrictMode>
);
