import React, { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { toast } from "sonner";
import { useDesk } from "@/context/DeskContext";
import { errText } from "@/lib/api";
import AuthShell, { AuthError, AuthLabel, Spinner } from "./AuthShell";
import { PasswordInput, StrengthMeter, strongEnough } from "@/components/brand/PasswordInput";

const EMAIL = /^[A-Za-z0-9._%+-]{1,64}@[A-Za-z0-9.-]{1,190}\.[A-Za-z]{2,24}$/;
const USER = /^[a-zA-Z0-9_.]{3,24}$/;

export default function Register() {
  const { register } = useDesk();
  const nav = useNavigate();
  const [f, setF] = useState({ name: "", username: "", email: "", password: "", confirm_password: "" });
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  const set = (k) => (v) => setF((o) => ({ ...o, [k]: typeof v === "string" ? v : v.target.value }));
  const local = () => {
    if (f.name.trim().length < 2) return "Please enter your full name.";
    if (!USER.test(f.username)) return "Username must be 3–24 letters, numbers, dots or underscores.";
    if (!EMAIL.test(f.email.trim())) return "Please enter a valid email address.";
    if (!strongEnough(f.password)) return "Please choose a stronger password (see the checklist).";
    if (f.password !== f.confirm_password) return "Passwords do not match.";
    return "";
  };
  const submit = async (e) => {
    e.preventDefault();
    const l = local();
    setErr(l);
    if (l) return;
    setBusy(true);
    try {
      const r = await register(f);
      if (r.pending) { toast.success(r.message || "Account created. An administrator must approve it before you can sign in."); nav("/login", { replace: true }); return; }
      toast.success(r.verification_email_sent ? "Account created. Check your inbox to verify your email." : "Account created. Verification email could not be sent — resend it from Account.");
      nav("/", { replace: true });
    } catch (x) { setErr(errText(x)); } finally { setBusy(false); }
  };
  return (
    <AuthShell title="Create your free account" sub="Standard user access · no fees, ever" wide>
      {(light) => {
        const ic = `auth-input ${light ? "auth-input-light" : ""}`;
        return (
          <form onSubmit={submit} className="space-y-3.5" data-testid="register-form" noValidate>
            <div><AuthLabel light={light}>Full name</AuthLabel><input value={f.name} onChange={set("name")} data-testid="register-name" autoComplete="name" className={ic} placeholder="Your full name" /></div>
            <div className="grid gap-3.5 sm:grid-cols-2">
              <div><AuthLabel light={light}>Username</AuthLabel><input value={f.username} onChange={set("username")} data-testid="register-username" autoComplete="username" className={ic} placeholder="e.g. govind_b" /></div>
              <div><AuthLabel light={light}>Email address</AuthLabel><input type="email" value={f.email} onChange={set("email")} data-testid="register-email" autoComplete="email" className={ic} placeholder="you@example.com" /></div>
            </div>
            <div><AuthLabel light={light}>Password</AuthLabel><PasswordInput value={f.password} onChange={set("password")} testid="register-password" placeholder="Create a strong password" light={light} autoComplete="new-password" /><StrengthMeter value={f.password} light={light} /></div>
            <div><AuthLabel light={light}>Confirm password</AuthLabel><PasswordInput value={f.confirm_password} onChange={set("confirm_password")} testid="register-confirm" placeholder="Repeat the password" light={light} autoComplete="new-password" />
              {f.confirm_password && f.confirm_password !== f.password && <p className="mt-1 text-[11px] text-red-500">Passwords do not match</p>}</div>
            <AuthError>{err}</AuthError>
            <button disabled={busy} data-testid="register-submit" className="auth-btn w-full">{busy ? <><Spinner /> Creating account…</> : "Create account"}</button>
            <p className={`text-center text-[13px] ${light ? "text-slate-600" : "text-slate-400"}`}>Already registered? <Link to="/login" data-testid="goto-login" className={`font-semibold hover:underline ${light ? "text-amber-700" : "text-amber-300"}`}>Sign in</Link></p>
          </form>
        );
      }}
    </AuthShell>
  );
}
