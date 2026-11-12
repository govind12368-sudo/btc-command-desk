import React, { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { useDesk } from "@/context/DeskContext";
import { errText } from "@/lib/api";
import AuthShell, { AuthError, AuthLabel, Spinner } from "./auth/AuthShell";
import { PasswordInput } from "@/components/brand/PasswordInput";
import { APP_NAME } from "@/config/brand";

export default function Login() {
  const { login } = useDesk();
  const nav = useNavigate();
  const [id, setId] = useState("");
  const [pw, setPw] = useState("");
  const [remember, setRemember] = useState(true);
  const [err, setErr] = useState("");
  const [otp, setOtp] = useState("");
  const [need2fa, setNeed2fa] = useState(false);
  const [busy, setBusy] = useState(false);
  const submit = async (e) => {
    e.preventDefault();
    setErr("");
    setBusy(true);
    try { await login(id.trim(), pw, remember, need2fa ? otp.trim() : undefined); nav("/", { replace: true }); }
    catch (x) {
      if (x?.response?.data?.detail === "2FA_REQUIRED") { setNeed2fa(true); setErr(""); }
      else setErr(errText(x));
    } finally { setBusy(false); }
  };
  return (
    <AuthShell title="Welcome back" sub={`Sign in to your ${APP_NAME} account`}>
      {(light) => (
        <form onSubmit={submit} className="space-y-4" data-testid="login-form">
          <div><AuthLabel light={light}>Username or email address</AuthLabel>
            <input value={id} onChange={(e) => setId(e.target.value)} data-testid="login-email" autoComplete="username" required placeholder="yourname or you@example.com" className={`auth-input ${light ? "auth-input-light" : ""}`} /></div>
          <div><AuthLabel light={light}>Password</AuthLabel><PasswordInput value={pw} onChange={setPw} testid="login-password" placeholder="Your password" light={light} /></div>
          {need2fa && <div><AuthLabel light={light}>Two-factor code</AuthLabel><input value={otp} onChange={(e) => setOtp(e.target.value)} data-testid="login-otp" autoComplete="one-time-code" inputMode="numeric" autoFocus required placeholder="6-digit code or a recovery code" className={`auth-input ${light ? "auth-input-light" : ""}`} /><p className="mt-1 text-[11px] opacity-70">Open your authenticator app and enter the current code.</p></div>}
          <div className="flex items-center justify-between text-[13px]">
            <label className={`flex cursor-pointer items-center gap-2 ${light ? "text-slate-600" : "text-slate-400"}`}>
              <input type="checkbox" checked={remember} onChange={(e) => setRemember(e.target.checked)} data-testid="login-remember" className="h-4 w-4 accent-amber-500" /> Remember me
            </label>
            <Link to="/forgot-password" data-testid="forgot-password-link" className={`font-medium hover:underline ${light ? "text-amber-700" : "text-amber-300"}`}>Forgot password?</Link>
          </div>
          <AuthError>{err}</AuthError>
          <button disabled={busy} data-testid="login-submit" className="auth-btn w-full">{busy ? <><Spinner /> Signing in…</> : "Sign in"}</button>
          <div className={`relative py-1 text-center text-[11px] uppercase tracking-wider ${light ? "text-slate-400" : "text-slate-500"}`}><span className="auth-divider">New here?</span></div>
          <Link to="/register" data-testid="create-account-button" className={`auth-btn-outline w-full ${light ? "auth-btn-outline-light" : ""}`}>Create New Account</Link>
        </form>
      )}
    </AuthShell>
  );
}
