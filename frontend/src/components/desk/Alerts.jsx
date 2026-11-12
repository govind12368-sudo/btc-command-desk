import React, { useState } from "react";
import { toast } from "sonner";
import { useFetch } from "@/hooks/useFetch";
import { api, errText } from "@/lib/api";
import { Card, Chip, Note } from "./ui";
import { Btn } from "./forms";
import { ist } from "@/lib/fmt";

const on = (s) => String(s || "").startsWith("configured");

function TelegramHelper() {
  const [chats, setChats] = useState(null);
  const find = async () => { try { setChats((await api.get("/alerts/telegram-chats")).data); } catch (e) { toast.error(errText(e)); } };
  return (
    <div className="mt-2">
      <Btn tone="ghost" data-testid="telegram-find-chat" onClick={find}>Find my Telegram chat ID</Btn>
      {chats && <p className="mt-1 font-mono text-[11px] text-slate-300">{Object.entries(chats.chats).map(([k, v]) => `${v}: ${k}`).join(" · ") || "No messages yet — send /start to your bot, then try again."}</p>}
    </div>
  );
}

function CallHelper() {
  return (
    <p className="mt-2 text-[11px] leading-relaxed text-slate-400" data-testid="call-helper">
      Phone-call alert setup: in Telegram, send <span className="font-mono">/start</span> to <span className="font-mono">@CallMeBot_txtbot</span> to allow calls,
      then set <span className="font-mono">CALLMEBOT_USER</span> (your Telegram @username or +91 number) in the backend environment and redeploy.
      The desk rings you and reads the signal aloud; at most one call per 5 minutes.
    </p>
  );
}


const CH_LABEL = { email: "Email", telegram: "Telegram", sms: "SMS", call: "Phone call", daily_recap: "Daily 8 PM recap" };

function AlertModePanel({ data, reload, isAdmin }) {
  const m = data?.mode;
  const [confirm, setConfirm] = useState("");
  const [busy, setBusy] = useState(false);
  if (!m) return null;
  const ready = { email: on(data.email) && !!data.email_to, telegram: on(data.telegram), sms: on(data.sms), call: on(data.call), daily_recap: on(data.email) || on(data.telegram) };
  const save = async (armed, channels) => {
    setBusy(true);
    try {
      await api.put("/alerts/mode", { armed, version: m.version, channels: channels || m.channels, confirm });
      toast.success(armed ? "Alert mode is ON — real alerts will be sent" : channels ? "Channels updated" : "Alert mode is OFF — alerts are dry-run only");
      setConfirm("");
      reload();
    } catch (e) { toast.error(errText(e)); } finally { setBusy(false); }
  };
  const armed = m.effective_armed;
  return (
    <div data-testid="alert-mode" className={`mb-4 rounded-xl border p-4 ${armed ? "border-teal-500/50 bg-teal-950/30" : "border-amber-500/40 bg-amber-950/20"}`}>
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-3">
          <span className={`relative flex h-3 w-3`}>
            {armed && <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-teal-400 opacity-60" />}
            <span className={`relative inline-flex h-3 w-3 rounded-full ${armed ? "bg-teal-400" : "bg-amber-400"}`} />
          </span>
          <div>
            <div className="text-sm font-semibold text-slate-100" data-testid="alert-mode-state">Alert mode: {armed ? "ON (armed)" : "OFF (dry run)"}</div>
            <div className="text-[11.5px] text-slate-400">
              {armed ? `Armed ${ist(m.armed_at)} by ${m.armed_by}. Real alerts go out on every captured signal.`
                : m.kill_switch ? "Forced OFF by ALERTS_KILL_SWITCH in the server environment."
                : "Everything is prepared. Signals are logged below as “dry run” — nothing is sent until you switch this ON."}
            </div>
          </div>
        </div>
        {isAdmin && (armed ? (
          <Btn tone="ghost" data-testid="alert-disarm" disabled={busy} onClick={() => save(false)}>Turn OFF</Btn>
        ) : (
          <div className="flex items-center gap-2">
            <input data-testid="alert-arm-confirm" value={confirm} onChange={(e) => setConfirm(e.target.value)} placeholder={`type ${data.confirm_word}`}
              className="w-24 rounded-md border border-[#1e2a4a] bg-[#0f172a] px-2 py-1.5 font-mono text-xs text-slate-100 outline-none focus:border-amber-400" disabled={m.kill_switch} />
            <Btn data-testid="alert-arm" disabled={busy || m.kill_switch || confirm.trim().toUpperCase() !== data.confirm_word} onClick={() => save(true)}>Turn ON alerts</Btn>
          </div>
        ))}
      </div>
      <div className="mt-3 grid grid-cols-2 gap-2 sm:grid-cols-5">
        {Object.keys(CH_LABEL).map((c) => (
          <label key={c} data-testid={`alert-ch-${c}`} className="flex cursor-pointer items-center justify-between gap-2 rounded-lg border border-[#1e2a4a] bg-[#0f172a]/70 px-2.5 py-2 text-[11.5px]">
            <span>
              <span className="block font-semibold text-slate-200">{CH_LABEL[c]}</span>
              <span className={ready[c] ? "text-teal-300" : "text-slate-500"}>{ready[c] ? "ready" : "not set up"}</span>
            </span>
            <input type="checkbox" className="h-4 w-4 accent-amber-500" checked={!!m.channels[c]} disabled={!isAdmin || busy}
              onChange={(e) => save(m.armed, { ...m.channels, [c]: e.target.checked })} />
          </label>
        ))}
      </div>
    </div>
  );
}

export function AlertsCard({ isAdmin }) {
  const { data, reload } = useFetch("/alerts", 30000);
  const test = async () => {
    try {
      const { data: r } = await api.post("/alerts/test");
      toast(`Email: ${r.result} · SMS: ${r.sms} · Telegram: ${r.telegram} · Call: ${r.call}`);
      reload();
    } catch (e) { toast.error(errText(e)); }
  };
  return (
    <Card title="Signal alerts" sub="When alert mode is ON, an alert is sent once per signal ID when every active Scalping or Swing check passes (11 checks; F31 excluded). Test alerts work in both modes." testid="alerts-card"
      right={isAdmin && <Btn data-testid="alert-test" onClick={test}>Send test alert</Btn>}>
      <AlertModePanel data={data} reload={reload} isAdmin={isAdmin} />
      <div className="flex flex-wrap gap-2">
        <Chip status={on(data?.email) && data?.email_to ? "PASS" : "UNAVAILABLE"} label={`EMAIL → ${data?.email_to || "not set"}`} testid="alert-email" />
        <Chip status={on(data?.sms) ? "PASS" : "UNAVAILABLE"} label={`SMS → ${data?.phone_to || "…"}`} testid="alert-sms" />
        <Chip status={on(data?.telegram) ? "PASS" : "UNAVAILABLE"} label="TELEGRAM" testid="alert-telegram" />
        <Chip status={on(data?.call) ? "PASS" : "UNAVAILABLE"} label="PHONE CALL" testid="alert-call" />
      </div>
      {data && <p className="mt-2 text-[11px] text-slate-500">Email: {data.email} · SMS: {data.sms} · Telegram: {data.telegram} · Phone call: {data.call || "not configured"}</p>}
      {isAdmin && data && !on(data.telegram) && <TelegramHelper />}
      {isAdmin && data && !on(data.call) && <CallHelper />}
      <div className="mt-2">
        {(data?.log || []).slice(0, 10).map((l) => (
          <div key={l.id} className="flex justify-between gap-2 border-b border-[#1e2a4a]/40 py-1 font-mono text-[11px]">
            <span className="truncate" title={l.preview || ""}>{ist(l.at)} · {l.channel || "email"} · {l.test ? "test" : l.signal_id}</span>
            <span className={String(l.result).startsWith("sent") ? "text-teal-300" : String(l.result).startsWith("dry run") || String(l.result).startsWith("skipped") ? "text-amber-300" : "text-red-300"}>{l.result}</span>
          </div>
        ))}
      </div>
    </Card>
  );
}

export function DailySummaryCard({ isAdmin }) {
  const { data, reload } = useFetch("/summary", 60000);
  const send = async () => {
    try { const { data: r } = await api.post("/summary/send-now"); toast(`Recap — email: ${r.result.email}, Telegram: ${r.result.telegram}`); reload(); } catch (e) { toast.error(errText(e)); }
  };
  return (
    <Card title="Daily recap (8 PM IST)" sub={data?.schedule} testid="daily-summary-card" right={isAdmin && <Btn data-testid="summary-send-now" onClick={send}>Send recap now</Btn>}>
      <pre className="max-h-56 overflow-auto scroll-thin whitespace-pre-wrap rounded bg-[#0f172a] p-2 font-mono text-[11px] text-slate-300" data-testid="summary-preview">{data?.preview || "Loading…"}</pre>
      {(data?.history || []).slice(0, 5).map((h) => <div key={h.id} className="mt-1 font-mono text-[11px] text-slate-400">{ist(h.at)} · {h.dry_run ? "dry run (alert mode OFF) · " : ""}email {h.result?.email} · telegram {h.result?.telegram}</div>)}
    </Card>
  );
}

export function DeltaSyncCard({ isAdmin, onDone }) {
  const { data, reload } = useFetch("/delta-sync", 30000);
  const run = async () => {
    try { const { data: r } = await api.post("/delta-sync/run"); toast(r.status === "ok" ? `Delta sync ok: ${r.imported} new, ${r.updated} updated` : `Delta sync: ${r.error}`); reload(); onDone && onDone(); } catch (e) { toast.error(errText(e)); }
  };
  if (!data) return null;
  return (
    <Card title="Delta read-only fill import" sub={data.mode} testid="delta-sync-card" right={isAdmin && data.configured && <Btn data-testid="delta-sync-run" onClick={run}>Sync now</Btn>}>
      <div className="flex flex-wrap items-center gap-2 text-xs">
        <Chip status={data.status === "ok" ? "PASS" : data.configured ? (data.status === "error" ? "BLOCKED" : "WAIT") : "UNAVAILABLE"} label={String(data.status).toUpperCase()} testid="delta-sync-status" />
        <span className="font-mono text-slate-400">last success {ist(data.last_success)} · raw fills {data.raw_fills} · new trades {data.imported} · updated {data.updated}</span>
      </div>
      {data.error && <Note tone="amber">{data.error}</Note>}
      <p className="mt-2 text-[11px] text-slate-500">Runs every 5 minutes while the server runs. {data.server_ip_note} Imported trades keep their Delta fill IDs (no duplicates); once you edit an imported trade, sync stops overwriting it.</p>
    </Card>
  );
}
