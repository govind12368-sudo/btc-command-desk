import React, { useEffect, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { api, errText } from "@/lib/api";
import AuthShell, { AuthError, AuthOk, AuthLabel, Spinner } from "./AuthShell";
import { PasswordInput, StrengthMeter, strongEnough } from "@/components/brand/PasswordInput";

const back = (light) => <p className={`mt-4 text-center text-[13px] ${light ? "text-slate-600" : "text-slate-400"}`}><Link to="/login" data-testid="back-to-login" className={`font-semibold hover:underline ${light ? "text-amber-700" : "text-amber-300"}`}>Back to sign in</Link></p>;

export function Forgot() {
  const [email, setEmail] = useState("");
  const [msg, setMsg] = useState("");
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  const submit = async (e) => {
    e.preventDefault(); setErr(""); setMsg(""); setBusy(true);
    try { setMsg((await api.post("/auth/forgot-password", { email: email.trim() })).data.message); } catch (x) { setErr(errText(x)); } finally { setBusy(false); }
  };
  return (
    <AuthShell title="Forgot your password?" sub="We'll email a secure, single-use reset link to your verified address">
      {(light) => (
        <form onSubmit={submit} className="space-y-4" data-testid="forgot-form">
          <div><AuthLabel light={light}>Email address or username</AuthLabel><input value={email} onChange={(e) => setEmail(e.target.value)} required data-testid="forgot-email" className={`auth-input ${light ? "auth-input-light" : ""}`} placeholder="you@example.com" /></div>
          <AuthError>{err}</AuthError><AuthOk>{msg}</AuthOk>
          <button disabled={busy} data-testid="forgot-submit" className="auth-btn w-full">{busy ? <><Spinner /> Sending…</> : "Send reset link"}</button>
          {back(light)}
        </form>
      )}
    </AuthShell>
  );
}

export function Reset() {
  const [sp] = useSearchParams();
  const token = sp.get("token") || "";
  const [pw, setPw] = useState("");
  const [cf, setCf] = useState("");
  const [msg, setMsg] = useState("");
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  const submit = async (e) => {
    e.preventDefault(); setErr("");
    if (!strongEnough(pw)) return setErr("Please choose a stronger password.");
    if (pw !== cf) return setErr("Passwords do not match.");
    setBusy(true);
    try { setMsg((await api.post("/auth/reset-password", { token, password: pw, confirm_password: cf })).data.message); } catch (x) { setErr(errText(x)); } finally { setBusy(false); }
  };
  return (
    <AuthShell title="Choose a new password" sub="Reset links expire after 30 minutes and work only once">
      {(light) => !token ? <><AuthError>This reset link is missing its token. Request a new one.</AuthError>{back(light)}</> : (
        <form onSubmit={submit} className="space-y-4" data-testid="reset-form">
          <div><AuthLabel light={light}>New password</AuthLabel><PasswordInput value={pw} onChange={setPw} testid="reset-password" light={light} autoComplete="new-password" /><StrengthMeter value={pw} light={light} /></div>
          <div><AuthLabel light={light}>Confirm new password</AuthLabel><PasswordInput value={cf} onChange={setCf} testid="reset-confirm" light={light} autoComplete="new-password" /></div>
          <AuthError>{err}</AuthError><AuthOk>{msg}</AuthOk>
          {!msg && <button disabled={busy} data-testid="reset-submit" className="auth-btn w-full">{busy ? <><Spinner /> Saving…</> : "Save new password"}</button>}
          {back(light)}
        </form>
      )}
    </AuthShell>
  );
}

export function VerifyEmail() {
  const [sp] = useSearchParams();
  const token = sp.get("token") || "";
  const [st, setSt] = useState({ busy: true, msg: "", err: "" });
  const done = useRef(false);
  useEffect(() => {
    if (done.current) return;
    done.current = true;
    if (!token) { setSt({ busy: false, msg: "", err: "This verification link is missing its token." }); return; }
    api.post("/auth/verify-email", { token }).then((r) => setSt({ busy: false, msg: r.data.message, err: "" })).catch((x) => setSt({ busy: false, msg: "", err: errText(x) }));
  }, [token]);
  return (
    <AuthShell title="Email verification">
      {(light) => (
        <div className="space-y-4" data-testid="verify-panel">
          {st.busy && <p className="text-center text-sm"><Spinner /> Verifying…</p>}
          <AuthError>{st.err}</AuthError><AuthOk>{st.msg}</AuthOk>
          <p className={`text-center text-[13px] ${light ? "text-slate-600" : "text-slate-400"}`}><Link to="/" data-testid="verify-continue" className={`font-semibold hover:underline ${light ? "text-amber-700" : "text-amber-300"}`}>Continue to BTC Command Desk</Link></p>
        </div>
      )}
    </AuthShell>
  );
}
