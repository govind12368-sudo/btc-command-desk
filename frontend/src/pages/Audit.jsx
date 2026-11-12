import React, { useState } from "react";
import { toast } from "sonner";
import { useDesk } from "@/context/DeskContext";
import { useFetch } from "@/hooks/useFetch";
import { api, API, errText } from "@/lib/api";
import { Card, Chip, PageHead, Empty, Note } from "@/components/desk/ui";
import { Btn } from "@/components/desk/forms";
import { WorkbookCoverage } from "@/components/desk/Workbook";
import { SecurityCard } from "@/components/desk/Security";
import { ist, ago } from "@/lib/fmt";

function RecoveryResult({ result }) {
  if (!result) return <Empty>No recovery pass recorded yet.</Empty>;
  const counts = result.counts;
  return <div className="mt-3 space-y-3" data-testid="recovery-result">
    <div className="flex flex-wrap items-center gap-2"><Chip status={result.result} label={String(result.result || "—").toUpperCase()} /><span className="font-mono text-[11px] text-slate-400">{result.run_id ? `Run ${result.run_id}` : result.last_run_id ? `Last run ${result.last_run_id}` : ""}</span></div>
    {result.retry_in_sec && <Note tone="amber">Recovery is cooling down. Try again in {result.retry_in_sec} seconds; provider retry windows still apply.</Note>}
    {result.result === "already running" && <Note tone="amber">A recovery pass is running. This page refreshes its audit status every 15 seconds.</Note>}
    {counts && <div className="grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-6 font-mono text-xs">
      {[["Checked", counts.checked], ["Attempted", counts.attempted], ["Fixed", counts.fixed], ["Still failing", counts.still_failing], ["Access / input needed", counts.configuration_required], ["Deferred", counts.deferred]].map(([label, value]) => <div key={label} className="rounded border border-[#1e2a4a] p-2"><div className="text-slate-500 text-[10px]">{label}</div><div className="mt-1 text-slate-200">{value ?? 0}</div></div>)}
    </div>}
    {result.at && <p className="font-mono text-[10.5px] text-slate-500">Started {ist(result.at)}{result.finished_at ? ` · finished ${ist(result.finished_at)}` : ""}{result.duration_sec != null ? ` · ${result.duration_sec}s` : ""}{result.recomputed ? " · dashboard recalculated" : ""}</p>}
    {(result.recompute_error || result.history_saved === false) && <Note tone="amber">{result.recompute_error ? `Dashboard recalculation needs attention: ${result.recompute_error}. ` : ""}{result.history_saved === false ? `This result is visible here, but storing its history failed: ${result.history_error || "storage unavailable"}.` : ""}</Note>}
    {result.actions?.length > 0 && <div className="overflow-x-auto scroll-thin"><table className="desk-table"><thead><tr><th>Source / action</th><th>Before</th><th>After</th><th>Outcome</th></tr></thead>
      <tbody>{result.actions.map((action) => <tr key={action.source} data-testid={`recovery-action-${action.source}`}>
        <td><span className="font-mono text-xs">{action.source}</span><span className="block text-[10.5px] text-slate-500">{action.action}{action.attempted ? "" : " · not retried"}</span></td>
        <td><Chip status={action.before?.status} /><span className="block font-mono text-[10px] text-slate-500">Last success {ist(action.before?.last_success || action.before?.last_msg)}</span>{action.before?.error && <span className="block text-[10.5px] text-slate-400">{action.before.error}</span>}</td>
        <td><Chip status={action.after?.status} /><span className="block font-mono text-[10px] text-slate-500">Last success {ist(action.after?.last_success || action.after?.last_msg)}</span>{action.after?.error && <span className="block text-[10.5px] text-slate-400">{action.after.error}</span>}</td>
        <td><span className="font-mono text-[11px]">{String(action.outcome || "—").replaceAll("_", " ")}</span>{action.reason && <span className="block text-[10.5px] text-slate-400">{action.reason}</span>}</td>
      </tr>)}</tbody></table></div>}
  </div>;
}

export default function Audit() {
  const { isAdmin } = useDesk();
  const { data: a, reload } = useFetch("/audit", 15000);
  const [busy, setBusy] = useState(false);
  const [latest, setLatest] = useState(null);
  const [runError, setRunError] = useState("");
  const run = async () => {
    if (busy) return;
    setBusy(true);
    setRunError("");
    try { const { data } = await api.post("/recovery/run", null, { timeout: 180000 }); setLatest(data); toast(`Recovery: ${data.result}${data.retry_in_sec ? ` (cooldown ${data.retry_in_sec}s)` : ""}`); await reload(); } catch (e) { const message = errText(e); setRunError(message); toast.error(message); } finally { setBusy(false); }
  };
  if (!a) return <Empty>Loading audit…</Empty>;
  const lastCompleted = a.recovery?.last_result;
  // Poll results supersede an in-progress response when its run completes.
  const shownResult = latest?.result === "already running" && lastCompleted?.run_id === latest.run_id ? lastCompleted : latest || lastCompleted;
  return (
    <div className="space-y-4">
      <PageHead title="System Audit" sub="Current source faults, storage health, release schedules, calculation dependencies, workbook coverage and repair history."
        right={isAdmin && <div className="flex flex-wrap gap-2"><a href={`${API}/source-download`} data-testid="download-source" className="rounded border border-teal-500/50 px-3 py-1.5 text-xs font-semibold text-teal-300 hover:bg-teal-500/10 transition-colors">Download source code (.zip)</a><Btn data-testid="run-recovery" disabled={busy || a.recovery?.state === "checking"} onClick={run}>{busy || a.recovery?.state === "checking" ? "Checking sources…" : "Check & repair all sources"}</Btn></div>} />
      <SecurityCard />
      <Card title="Source audit & recovery" sub="The button checks all failing and stale installed sources. Automatic background recovery retries one due source family at a time. 120 s cooldown, bounded retries and provider Retry-After are respected." testid="recovery-card"
        right={<Chip status={a.recovery?.state} label={String(a.recovery?.state || "idle").toUpperCase()} testid="recovery-state" />}>
        <p className="font-mono text-xs text-slate-400">Last pass {ist(a.recovery?.last_pass)} · result {a.recovery?.last_result?.result || "—"} {a.recovery?.last_result?.family ? `(${a.recovery.last_result.family})` : ""}</p>
        <p className="mt-1 text-[11px] text-slate-500">Recovery never invents values, changes observation dates, overrides risk settings or confirmations, rewrites trades, runs arbitrary URLs/code, or claims an authentication restriction was repaired.</p>
        <RecoveryResult result={shownResult} />
        {runError && <Note tone="red">Recovery request failed: {runError}. Check the current audit status below; a failed request does not prove the sources were repaired.</Note>}
        {latest && lastCompleted && shownResult !== lastCompleted && latest.run_id !== lastCompleted.run_id && <details className="mt-3 text-xs text-slate-400"><summary className="cursor-pointer">Last completed recovery pass</summary><RecoveryResult result={lastCompleted} /></details>}
      </Card>
      <Card title={`Current issues (${a.current_issues.length})`} testid="current-issues">
        {a.current_issues.length === 0 ? <Empty>All sources healthy.</Empty> : a.current_issues.map((s) => (
          <div key={s.key} className="py-2 border-b border-[#1e2a4a]/50">
            <div className="flex flex-wrap items-center gap-2"><Chip status={s.status} /><span className="text-sm">{s.name}</span><span className="font-mono text-[10.5px] text-slate-500">{s.instrument}</span></div>
            <p className="mt-1 font-mono text-[11px] text-red-300/90">{s.error || "data older than its freshness threshold"}</p>
            <p className="font-mono text-[10.5px] text-slate-500">last success {ist(s.last_success)} · failures {s.fail_count} · next retry {ist(s.next_retry)}{s.status === "access_needed" ? " · provider access/input needed (cannot be repaired automatically)" : ""}</p>
          </div>
        ))}
      </Card>
      <Card title="Data-source registry" testid="source-registry">
        <div className="overflow-x-auto scroll-thin"><table className="desk-table"><thead><tr><th>Source</th><th>Instrument</th><th>Unit</th><th>Interval</th><th>Fresh if &lt;</th><th>Last success</th><th>Status</th></tr></thead>
          <tbody>{a.sources.map((s) => <tr key={s.key}><td><a className="hover:text-amber-300" href={s.url?.startsWith("http") ? s.url : undefined} target="_blank" rel="noreferrer">{s.name}</a></td><td className="font-mono text-[11px]">{s.instrument}</td><td className="text-[11px]">{s.unit}</td><td className="font-mono">{s.interval_sec ? `${s.interval_sec}s` : "stream"}</td><td className="font-mono">{s.freshness_sec}s</td><td className="font-mono text-[11px]">{ago(s.last_success)}</td><td><Chip status={s.status} /></td></tr>)}</tbody></table></div>
      </Card>
      <div className="grid gap-4 lg:grid-cols-2">
        <Card title="Storage health" sub={a.storage.limits} testid="storage-card" right={<Chip status={a.storage.ok ? "PASS" : "BLOCKED"} />}>
          <div className="grid grid-cols-2 gap-x-6 font-mono text-[12px]">{Object.entries(a.storage.collections).map(([k, v]) => <div key={k} className="flex justify-between py-0.5 border-b border-[#1e2a4a]/40"><span className="text-slate-400">{k}</span><span>{v}</span></div>)}</div>
          <p className="mt-2 font-mono text-[11px] text-slate-500">data size {a.storage.data_mb} MB</p>
        </Card>
        <Card title="Calculation dependencies" testid="dependencies-card">
          {a.dependencies.map((d) => <div key={d.output} className="py-1.5 border-b border-[#1e2a4a]/40"><div className="text-sm text-slate-200">{d.output}</div><div className="font-mono text-[11px] text-slate-500">← {d.inputs.join(" · ")}</div></div>)}
        </Card>
      </div>
      <Card title="Release schedules (macro)" testid="release-schedules">
        {Object.entries(a.release_schedules || {}).map(([k, m]) => <div key={k} className="flex flex-wrap items-center gap-2 py-1.5 border-b border-[#1e2a4a]/40 text-xs"><span className="font-mono text-amber-300 w-14">{k}</span><span>{m.schedule}</span><span className="font-mono text-slate-400">release {m.release_date} · next {m.next_publication}{m.next_publication_inferred ? " (inferred)" : " (official)"} · deadline {ist(m.deadline)}</span><Chip status={m.valid ? "PASS" : "UNAVAILABLE"} /></div>)}
        <p className="mt-2 text-[11px] text-slate-500">H.10: weekly Monday release of prior-week daily observations. H.15: business-day releases. EIA Brent: weekly batches of daily observations (next date read from EIA). Holiday extensions are never assumed.</p>
      </Card>
      <WorkbookCoverage wb={a.workbook} note={a.historical_findings_note} />
      <Card title="Repair history" sub="Stored separately from trades and reviews" testid="repair-history">
        {a.repair_history.length === 0 ? <Empty>No repair actions yet.</Empty> : (
          <div className="overflow-x-auto scroll-thin"><table className="desk-table"><thead><tr><th>Time / run</th><th>Family</th><th>Action</th><th>Result</th></tr></thead>
            <tbody>{a.repair_history.map((r) => <tr key={r.id || r.run_id}><td className="font-mono text-[11px]">{ist(r.at)}<span className="block text-[10px] text-slate-500">{r.run_id || "legacy pass"}</span></td><td className="font-mono">{r.family}</td><td className="text-xs">{r.action}{r.actions?.length > 0 && <details className="mt-1"><summary className="cursor-pointer text-amber-300">Source evidence</summary><RecoveryResult result={r} /></details>}</td><td><Chip status={r.result} label={String(r.result).toUpperCase()} />{r.counts && <span className="block text-[10.5px] text-slate-400 mt-1">{r.counts.fixed} fixed · {r.counts.still_failing} failing · {r.counts.configuration_required} need input · {r.counts.deferred} deferred</span>}</td></tr>)}</tbody></table></div>
        )}
      </Card>
      {a.compute_error && <Note tone="red">Last calculation error: {a.compute_error}</Note>}
    </div>
  );
}
