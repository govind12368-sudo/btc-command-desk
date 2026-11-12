import React, { useState } from "react";
import { toast } from "sonner";
import { useFetch } from "@/hooks/useFetch";
import { api, errText } from "@/lib/api";
import { useDesk } from "@/context/DeskContext";
import { Card, Chip, Note } from "./ui";
import { Btn, Input } from "./forms";
import { ist } from "@/lib/fmt";

function TwoFactor() {
  const { data, reload } = useFetch("/auth/2fa", 0);
  const { refreshUser } = useDesk();
  const [setup, setSetup] = useState(null);
  const [code, setCode] = useState("");
  const [codes, setCodes] = useState(null);
  const [off, setOff] = useState({ password: "", code: "" });
  const start = async () => { try { setSetup((await api.post("/auth/2fa/setup")).data); setCodes(null); } catch (e) { toast.error(errText(e)); } };
  const enable = async () => {
    try { const { data: r } = await api.post("/auth/2fa/enable", { code }); setCodes(r.recovery_codes); setSetup(null); setCode(""); toast.success("Two-factor authentication is on"); reload(); refreshUser && refreshUser(); }
    catch (e) { toast.error(errText(e)); }
  };
  const disable = async () => {
    if (!window.confirm("Turn off two-factor authentication for the admin account?")) return;
    try { await api.post("/auth/2fa/disable", off); setOff({ password: "", code: "" }); toast("Two-factor authentication is off"); reload(); refreshUser && refreshUser(); }
    catch (e) { toast.error(errText(e)); }
  };
  if (!data) return null;
  return (
    <div data-testid="twofa-card">
      <div className="flex items-center gap-2"><span className="font-semibold">Two-factor authentication (admin)</span><Chip status={data.enabled ? "PASS" : "FAIL"} label={data.enabled ? "ON" : "OFF"} testid="twofa-status" /></div>
      {codes && (
        <div className="mt-3 rounded border border-amber-500/40 bg-amber-500/10 p-3" data-testid="twofa-recovery-codes">
          <p className="text-sm text-amber-200">Save these 8 recovery codes somewhere safe and offline. Each works once if you lose your phone. They will not be shown again.</p>
          <div className="mt-2 grid grid-cols-2 gap-1 font-mono text-sm sm:grid-cols-4">{codes.map((c) => <span key={c}>{c}</span>)}</div>
        </div>
      )}
      {!data.enabled && !setup && <div className="mt-3"><Btn data-testid="twofa-start" onClick={start}>Set up two-factor authentication</Btn></div>}
      {!data.enabled && setup && (
        <div className="mt-3 grid gap-4 md:grid-cols-[220px_1fr]">
          <div className="rounded bg-white p-2" data-testid="twofa-qr" dangerouslySetInnerHTML={{ __html: setup.qr_svg }} />
          <div className="space-y-2 text-sm">
            <p>{setup.note}</p>
            <p className="font-mono text-xs break-all">Secret: {setup.secret}</p>
            <div className="flex gap-2 items-end"><div className="w-40"><Input data-testid="twofa-code" value={code} onChange={(e) => setCode(e.target.value)} placeholder="6-digit code" /></div><Btn data-testid="twofa-enable" onClick={enable}>Turn on</Btn></div>
          </div>
        </div>
      )}
      {data.enabled && (
        <div className="mt-3 space-y-2 text-sm">
          <p className="text-slate-400">Recovery codes left: {data.recovery_codes_left}</p>
          <div className="flex flex-wrap gap-2 items-end">
            <div className="w-48"><Input type="password" placeholder="Admin password" value={off.password} onChange={(e) => setOff({ ...off, password: e.target.value })} /></div>
            <div className="w-40"><Input placeholder="Current code" value={off.code} onChange={(e) => setOff({ ...off, code: e.target.value })} /></div>
            <Btn tone="red" data-testid="twofa-disable" onClick={disable}>Turn off</Btn>
          </div>
        </div>
      )}
    </div>
  );
}

function Events() {
  const { data } = useFetch("/security/events?limit=200", 30000);
  const rows = data?.events || [];
  return (
    <div data-testid="security-events">
      <div className="font-semibold">Security events <span className="text-xs font-normal text-slate-500">(latest 200 · kept {data?.retention_days ?? 90} days)</span></div>
      {(data?.config_warnings || []).map((w, i) => <div key={i} className="mt-2"><Note tone="amber">{w}</Note></div>)}
      <div className="mt-2 max-h-80 overflow-y-auto scroll-thin"><table className="desk-table">
        <thead><tr><th>Time (IST)</th><th>Event</th><th>Account</th><th>IP</th><th>Detail</th></tr></thead>
        <tbody>{rows.map((e) => (
          <tr key={e.id}><td className="font-mono text-[11px]">{ist(e.at)}</td><td><Chip status={e.ok ? "PASS" : "FAIL"} label={e.kind} /></td><td className="font-mono text-[11px]">{e.user || "—"}</td><td className="font-mono text-[11px]">{e.ip || "—"}</td><td className="text-[11px] text-slate-400">{e.detail || ""}</td></tr>
        ))}</tbody>
      </table></div>
    </div>
  );
}

export function SecurityCard() {
  return (
    <Card title="Security" sub="Admin two-factor login and the security event log" testid="security-card">
      <div className="space-y-6"><TwoFactor /><Events /></div>
    </Card>
  );
}
