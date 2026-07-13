/* Real authentication — email + password login, forced-reset screen on MUST_RESET,
   session context, logout. Session survives refresh via the stored refresh token. */

import { createContext, useContext, useEffect, useState } from "react";
import { api, clearTokens, currentUserId, getRefreshToken, setTokens } from "./api.js";

const AuthCtx = createContext(null);
export const useAuth = () => useContext(AuthCtx);

export function AuthProvider({ children }) {
  const [status, setStatus] = useState("loading"); // loading | anon | must_reset | authed
  const [me, setMe] = useState(null);
  const [firm, setFirm] = useState(null);

  const loadSession = async () => {
    try {
      const users = await api.get("/users");
      const mine = users.find((u) => u.id === currentUserId());
      const firmData = await api.get("/tenants/me");
      setMe(mine || null);
      setFirm(firmData);
      setStatus(mine ? "authed" : "anon");
    } catch (e) {
      if (e.status === 403 && e.detail?.code === "MUST_RESET") setStatus("must_reset");
      else if (e.status === 401) {
        clearTokens();
        setStatus("anon");
      } else {
        // transient failure (network blip, API restarting) — keep the session, retry;
        // dropping to anon here silently logged users out on hard refresh
        setTimeout(loadSession, 3000);
      }
    }
  };

  useEffect(() => {
    if (getRefreshToken()) loadSession();
    else setStatus("anon");
  }, []);

  const login = async (email, password) => {
    const tokens = await api.public.post("/auth/login", { email, password });
    setTokens(tokens);
    if (tokens.must_reset) setStatus("must_reset");
    else await loadSession();
  };

  const resetPassword = async (newPassword) => {
    const tokens = await api.post("/auth/reset-password", { new_password: newPassword });
    setTokens(tokens);
    await loadSession();
  };

  const logout = () => {
    clearTokens();
    setMe(null);
    setFirm(null);
    setStatus("anon");
  };

  return (
    <AuthCtx.Provider value={{ status, me, firm, login, resetPassword, logout, reloadFirm: loadSession }}>
      {children}
    </AuthCtx.Provider>
  );
}

const inputCls = "mt-1 w-full border rounded-md px-3 py-2 text-sm";

export function LoginScreen() {
  const { login } = useAuth();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);

  const submit = async () => {
    if (!email.trim() || !password) return;
    setBusy(true);
    setErr("");
    try {
      await login(email.trim(), password);
    } catch (e) {
      setErr(e.status === 401 ? "Invalid email or password." : e.message);
    }
    setBusy(false);
  };

  return (
    <div className="min-h-screen flex items-center justify-center" style={{ background: "var(--ink)" }}>
      <div className="w-full max-w-sm px-6">
        <div className="font-disp text-white text-3xl tracking-tight">Baton</div>
        <div className="mt-1 text-sm" style={{ color: "#8FA3B8" }}>CRM &amp; employee performance tracking for bookkeeping and tax firms</div>
        <div className="mt-8 p-5 rounded-2xl bg-white/95">
          <label className="text-xs font-semibold" style={{ color: "var(--mut)" }}>Email</label>
          <input value={email} onChange={(e) => setEmail(e.target.value)} type="email" autoComplete="username"
                 className={inputCls} style={{ borderColor: "var(--line)" }} />
          <label className="text-xs font-semibold mt-3 block" style={{ color: "var(--mut)" }}>Password</label>
          <input value={password} onChange={(e) => setPassword(e.target.value)} type="password" autoComplete="current-password"
                 onKeyDown={(e) => e.key === "Enter" && submit()} className={inputCls} style={{ borderColor: "var(--line)" }} />
          {err && <div className="mt-3 text-xs px-2.5 py-2 rounded-md font-medium" style={{ background: "var(--red-soft)", color: "var(--red)" }}>{err}</div>}
          <button onClick={submit} disabled={busy || !email.trim() || !password}
                  className="mt-4 w-full px-4 py-2.5 rounded-lg text-white text-sm font-semibold disabled:opacity-40" style={{ background: "var(--accent)" }}>
            {busy ? "Signing in…" : "Sign in"}
          </button>
        </div>
      </div>
    </div>
  );
}

export function ForcedResetScreen() {
  const { resetPassword, logout } = useAuth();
  const [pw, setPw] = useState("");
  const [pw2, setPw2] = useState("");
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);

  const submit = async () => {
    if (pw.length < 8) return setErr("Password must be at least 8 characters.");
    if (pw !== pw2) return setErr("Passwords do not match.");
    setBusy(true);
    setErr("");
    try {
      await resetPassword(pw);
    } catch (e) {
      setErr(e.message);
      setBusy(false);
    }
  };

  return (
    <div className="min-h-screen flex items-center justify-center" style={{ background: "var(--ink)" }}>
      <div className="w-full max-w-sm px-6">
        <div className="font-disp text-white text-2xl tracking-tight">Set your password</div>
        <div className="mt-1 text-sm" style={{ color: "#8FA3B8" }}>
          First login (or a reset was required). Choose a new password before continuing — the temporary one stops working now.
        </div>
        <div className="mt-6 p-5 rounded-2xl bg-white/95">
          <label className="text-xs font-semibold" style={{ color: "var(--mut)" }}>New password (min 8 characters)</label>
          <input value={pw} onChange={(e) => setPw(e.target.value)} type="password" autoComplete="new-password"
                 className={inputCls} style={{ borderColor: "var(--line)" }} />
          <label className="text-xs font-semibold mt-3 block" style={{ color: "var(--mut)" }}>Repeat password</label>
          <input value={pw2} onChange={(e) => setPw2(e.target.value)} type="password" autoComplete="new-password"
                 onKeyDown={(e) => e.key === "Enter" && submit()} className={inputCls} style={{ borderColor: "var(--line)" }} />
          {err && <div className="mt-3 text-xs px-2.5 py-2 rounded-md font-medium" style={{ background: "var(--red-soft)", color: "var(--red)" }}>{err}</div>}
          <button onClick={submit} disabled={busy} className="mt-4 w-full px-4 py-2.5 rounded-lg text-white text-sm font-semibold disabled:opacity-40" style={{ background: "var(--accent)" }}>
            {busy ? "Saving…" : "Set password & continue"}
          </button>
          <button onClick={logout} className="mt-3 w-full text-[11px] underline" style={{ color: "var(--mut)" }}>Back to login</button>
        </div>
      </div>
    </div>
  );
}
