import { StrictMode, Suspense, lazy } from "react";
import { createRoot } from "react-dom/client";
import "./index.css";

// VITE_DEMO_MODE=true → the self-contained in-memory demo (the public portfolio site).
// Production build → the API-backed app (real login, server state).
// /platform → the Platform Operator console (the developer's own login above all tenants).
const DEMO = import.meta.env.VITE_DEMO_MODE === "true";
const PLATFORM = window.location.pathname.startsWith("/platform");
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
