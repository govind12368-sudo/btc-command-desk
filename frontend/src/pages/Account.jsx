import React, { useState } from "react";
import { toast } from "sonner";
import { useDesk } from "@/context/DeskContext";
import { api, errText, setToken } from "@/lib/api";
import { Card, Chip, KV, PageHead, Note } from "@/components/desk/ui";
import { Btn } from "@/components/desk/forms";
import { PasswordInput, StrengthMeter, strongEnough } from "@/components/brand/PasswordInput";
import { ist } from "@/lib/fmt";

function ChangePassword() {
  const [f, setF] = useState({ current_password: "", new_password: "", confirm_password: "" });
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const set = (k) => (v) => setF((o) => ({ ...o, [k]: v }));
  const save = async (e) => {
    e.preventDefault(); setErr("");
    if (!strongEnough(f.new_password)) return setErr("New password is not strong enough.");
    if (f.new_password !== f.confirm_password) return setErr("New passwords do not match.");
    setBusy(true);
    try {
      const { data } = await api.post("/auth/change-password", f);
      setToken(data.token);
      toast.success(data.message);
      setF({ current_password: "", new_password: "", confirm_password: "" });
    } catch (x) { setErr(errText(x)); toast.error(errText(x)); } finally { setBusy(false); }
  };
  return (
    <Card title="Change password" sub="Requires your current password. Other signed-in devices are signed out." testid="change-password-card">
      <form onSubmit={save} className="max-w-md space-y-3">
        <div><label className="mb-1 block text-xs text-slate-400">Current password</label><PasswordInput value={f.current_password} onChange={set("current_password")} testid="cp-current" /></div>
        <div><label className="mb-1 block text-xs text-slate-400">New password</label><PasswordInput value={f.new_password} onChange={set("new_password")} testid="cp-new" autoComplete="new-password" /><StrengthMeter value={f.new_password} /></div>
        <div><label className="mb-1 block text-xs text-slate-400">Confirm new password</label><PasswordInput value={f.confirm_password} onChange={set("confirm_password")} testid="cp-confirm" autoComplete="new-password" /></div>
        {err && <p className="text-xs text-red-400" data-testid="cp-error">{err}</p>}
        <Btn type="submit" disabled={busy} data-testid="cp-save">{busy ? "Saving…" : "Save New Password"}</Btn>
      </form>
    </Card>
  );
}

function Profile() {
  const { user, setUser } = useDesk();
  const [name, setName] = useState(user.name || "");
  const save = async () => { try { const { data } = await api.put("/auth/profile", { name }); setUser((u) => ({ ...u, ...data })); toast.success("Profile saved"); } catch (x) { toast.error(errText(x)); } };
  const resend = async () => { try { toast.success((await api.post("/auth/resend-verification")).data.message); } catch (x) { toast.error(errText(x)); } };
  return (
    <Card title="Profile" testid="profile-card">
      <KV k="Username" v={user.username || "—"} testid="profile-username" />
      <KV k="Email" v={user.email} testid="profile-email" />
      <KV k="Role" v={user.role === "admin" ? "Administrator" : "User (view only)"} testid="profile-role" />
      <KV k="Member since" v={ist(user.created_at)} />
      <div className="mt-2 flex items-center gap-2"><span className="text-xs text-slate-400">Email</span>
        <Chip status={user.email_verified ? "PASS" : "WAIT"} label={user.email_verified ? "VERIFIED" : "NOT VERIFIED"} testid="profile-verified" />
        {!user.email_verified && <Btn tone="ghost" data-testid="resend-verification" onClick={resend}>Resend verification email</Btn>}
      </div>
      <div className="mt-4 flex max-w-md items-end gap-2">
        <div className="flex-1"><label className="mb-1 block text-xs text-slate-400">Full name</label>
          <input value={name} onChange={(e) => setName(e.target.value)} data-testid="profile-name" className="w-full rounded border border-[#1e2a4a] bg-[#0f172a] px-2.5 py-1.5 text-sm" /></div>
        <Btn data-testid="profile-save" onClick={save}>Save</Btn>
      </div>
    </Card>
  );
}

export default function Account() {
  return (
    <div className="space-y-4">
      <PageHead title="Account Settings" sub="Your profile and password. You can only see and change your own account." />
      <div className="grid gap-4 lg:grid-cols-2"><Profile /><ChangePassword /></div>
      <Note>Forgot your password while signed out? Use “Forgot password?” on the sign-in page — a single-use link valid for 30 minutes is emailed to you.</Note>
    </div>
  );
}
