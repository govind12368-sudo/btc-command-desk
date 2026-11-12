import React, { useEffect, useState } from "react";
import { toast } from "sonner";
import { useDesk } from "@/context/DeskContext";
import { useFetch } from "@/hooks/useFetch";
import { api, errText } from "@/lib/api";
import { Card, Chip, PageHead, Empty, Note } from "@/components/desk/ui";
import { Field, Input, Select, Btn, Area } from "@/components/desk/forms";
import { ist, ago } from "@/lib/fmt";

const CATS = ["", "Macro & central banks", "Geopolitical risk", "Regulation & legal", "ETF & institutional", "Security & exchanges", "Bitcoin", "Business & global"];

function DailyReview({ isAdmin }) {
  const { data, reload } = useFetch("/review", 10000);
  const [busy, setBusy] = useState(false);
  const save = async (patch) => {
    setBusy(true);
    try { await api.put("/review", { version: data.version || 0, ...patch }); toast.success("Saved"); await reload(); } catch (e) { toast.error(errText(e)); await reload(); } finally { setBusy(false); }
  };
  if (!data) return <Card title="Daily review · information only"><Empty>Loading…</Empty></Card>;
  const items = [["risk", "Risk limits reviewed"], ["news", "News reviewed"], ["venue", "Venue/contract checked"], ["journal", "Journal up to date"]];
  return (
    <Card title={`Daily review — ${data.id} IST · information only`} sub="These review statuses are personal notes. They do not change readiness, trade decisions, BTC Direction, swing/scalp eligibility or Trade Planner guardrails." testid="daily-review" right={<Chip status={data.status?.status} label={`NOTES: ${data.status?.status || "UNAVAILABLE"}`} testid="review-status" />}>
      <div className="grid gap-2 sm:grid-cols-2">
        {items.map(([k, label]) => (
          <div key={k} className="flex items-center justify-between rounded border border-[#1e2a4a] px-3 py-2">
            <div><div className="text-sm">{label}</div><div className="font-mono text-[10px] text-slate-500">{data[k]?.ok ? `confirmed ${ist(data[k].at)} by ${data[k].by}` : "not confirmed"}</div></div>
            {isAdmin && <Btn data-testid={`review-${k}`} tone={data[k]?.ok ? "ghost" : "teal"} disabled={busy} onClick={() => save({ [k]: !data[k]?.ok })}>{data[k]?.ok ? "Undo" : "Confirm"}</Btn>}
          </div>
        ))}
        {[["event_decision", "Economic event decision"], ["flow_decision", "Large-flow decision"]].map(([k, label]) => (
          <div key={k} className="flex items-center justify-between rounded border border-[#1e2a4a] px-3 py-2">
            <div><div className="text-sm">{label}</div><div className="font-mono text-[10px] text-slate-500">{data[k] ? `set ${ist(data[k + "_at"])}` : "not set"}</div></div>
            {isAdmin ? <div className="w-32"><Select data-testid={`review-${k}`} value={data[k] || ""} disabled={busy} onChange={(e) => e.target.value && save({ [k]: e.target.value })} options={[{ value: "", label: "— choose —" }, "PASS", "REVIEW", "BLOCKED"]} /></div> : <Chip status={data[k] || "UNAVAILABLE"} />}
          </div>
        ))}
      </div>
      <p className="mt-3 font-mono text-[11px] text-slate-400" data-testid="review-detail">{data.status?.detail}</p>
    </Card>
  );
}

function Events({ isAdmin }) {
  const { data, reload } = useFetch("/events", 60000);
  const blank = { title: "", scheduled_ist: "", impact: "HIGH", source_url: "", reviewed: false, notes: "" };
  const [f, setF] = useState(blank);
  const [edit, setEdit] = useState(null);
  const save = async () => {
    try {
      if (edit) await api.put(`/events/${edit}`, f); else await api.post("/events", f);
      toast.success("Event saved"); setF(blank); setEdit(null); reload();
    } catch (e) { toast.error(errText(e)); }
  };
  const del = async (id) => { try { await api.delete(`/events/${id}`); reload(); } catch (e) { toast.error(errText(e)); } };
  const cal = data?.calendar;
  const coverage = data?.calendar_coverage || cal || {};
  const providers = coverage.providers || [];
  const complete = coverage.coverage_complete === true;
  return (
    <Card title="Economic events · BLS, BEA & Federal Reserve" sub={data?.note} testid="events-card" right={<Chip status={cal?.status} label={`US CALENDAR ${cal?.status || "—"}`} />}>
      {(!complete || cal?.status !== "ok") && <Note tone="amber">Calendar coverage is incomplete. {coverage.coverage || cal?.error || "Some official schedules are unavailable."} An empty list does not establish that no market-moving events are scheduled.</Note>}
      <p className="mt-2 text-[11px] text-slate-400">Official scheduled release times are shown in IST, with each publisher and source linked below. {coverage.timezone_note} {coverage.source_note}</p>
      {providers.length > 0 && <div className="grid gap-2 mt-3 sm:grid-cols-3" data-testid="calendar-provider-coverage">
        {providers.map((p) => <div key={p.publisher} className="rounded border border-[#1e2a4a] px-3 py-2">
          <div className="flex items-center justify-between gap-2"><a className="text-xs text-amber-300" href={p.source_url} target="_blank" rel="noreferrer">{p.publisher}</a><Chip status={p.status} /></div>
          <p className="mt-1 font-mono text-[10.5px] text-slate-500">{p.events_in_window ?? 0} timed events · retrieved {ist(p.retrieved_at)}</p>
          {(p.error || p.errors?.length > 0) && <p className="mt-1 text-[11px] text-amber-300">{p.error || p.errors.join("; ")}</p>}
        </div>)}
      </div>}
      {isAdmin && (
        <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-5 items-end my-3">
          <Field label="Title"><Input data-testid="event-title" value={f.title} onChange={(e) => setF({ ...f, title: e.target.value })} /></Field>
          <Field label="Scheduled (IST)"><Input data-testid="event-time" type="datetime-local" value={f.scheduled_ist} onChange={(e) => setF({ ...f, scheduled_ist: e.target.value })} /></Field>
          <Field label="Impact"><Select data-testid="event-impact" value={f.impact} onChange={(e) => setF({ ...f, impact: e.target.value })} options={["LOW", "MEDIUM", "HIGH", "CRITICAL"]} /></Field>
          <Field label="Source URL"><Input data-testid="event-url" value={f.source_url} onChange={(e) => setF({ ...f, source_url: e.target.value })} placeholder="https://" /></Field>
          <div className="flex gap-2 items-center"><label className="flex items-center gap-1 text-xs"><input data-testid="event-reviewed" type="checkbox" checked={f.reviewed} onChange={(e) => setF({ ...f, reviewed: e.target.checked })} /> reviewed</label><Btn data-testid="event-save" onClick={save}>{edit ? "Update" : "Add"}</Btn></div>
        </div>
      )}
      <div className="overflow-x-auto scroll-thin"><table className="desk-table"><thead><tr><th>Scheduled (IST)</th><th>Event</th><th>Impact</th><th>Reviewed</th><th>Source</th><th /></tr></thead>
        <tbody>
          {(data?.manual || []).map((e) => <tr key={e.id}><td className="font-mono">{ist(e.scheduled_utc)}</td><td>{e.title}</td><td><Chip status={e.impact} /></td><td>{e.reviewed ? "yes" : "no"}</td><td><a className="text-amber-400 text-xs" href={e.source_url} target="_blank" rel="noreferrer">link</a></td>
            <td>{isAdmin && <div className="flex gap-1"><Btn tone="ghost" data-testid={`event-edit-${e.id}`} onClick={() => { setEdit(e.id); setF({ title: e.title, scheduled_ist: e.scheduled_ist, impact: e.impact, source_url: e.source_url, reviewed: e.reviewed, notes: e.notes || "" }); }}>Edit</Btn><Btn tone="red" data-testid={`event-del-${e.id}`} onClick={() => del(e.id)}>Delete</Btn></div>}</td></tr>)}
          {(data?.calendar_data || []).map((e, i) => <tr key={e.id || `calendar-${i}`}><td className="font-mono">{e.date_only ? `${String(e.time || "").slice(0, 10)} · time unavailable` : ist(e.time)}</td><td>{e.title} <span className="text-[10px] text-slate-500">({e.publisher || "official schedule"})</span></td><td><Chip status={e.impact || "MEDIUM"} /><span className="block text-[10px] text-slate-500">{e.impact_basis || "calendar classification"}</span></td><td>—</td><td><a className="text-amber-400 text-xs" href={e.source_url} target="_blank" rel="noreferrer">{e.publisher || "Official source"}</a><span className="block font-mono text-[10px] text-slate-500">retrieved {ist(e.retrieved_at)}</span></td><td /></tr>)}
        </tbody></table></div>
      {data && !(data.calendar_data?.length || data.manual?.length) && <Empty>No timed events returned in this window. Check coverage above and consult the official schedules.</Empty>}
    </Card>
  );
}

export default function News() {
  const { isAdmin } = useDesk();
  const [cat, setCat] = useState("");
  const [imp, setImp] = useState("");
  const [page, setPage] = useState(1);
  useEffect(() => setPage(1), [cat, imp]);
  const q = `/news?page=${page}&size=25${cat ? `&category=${encodeURIComponent(cat)}` : ""}${imp ? `&impact=${imp}` : ""}`;
  const { data, reload } = useFetch(q, 30000);
  const mark = async (id, status) => { try { await api.put(`/news/${id}/review`, { status }); reload(); } catch (e) { toast.error(errText(e)); } };
  return (
    <div className="space-y-4">
      <PageHead title="News & Events" sub="Information only. Headlines, event windows, calendar coverage and review confirmations do not affect readiness, trade decisions, BTC Direction or Trade Planner. Free publisher RSS and official BLS, BEA and Fed schedules remain available; headline impact uses keyword rules." />
      <DailyReview isAdmin={isAdmin} />
      <Card title="Global News Radar" sub={data?.classification_note} testid="news-list">
        <div className="flex flex-wrap gap-2 mb-3">
          <div className="w-56"><Select data-testid="news-category" value={cat} onChange={(e) => setCat(e.target.value)} options={CATS.map((c) => ({ value: c, label: c || "All categories" }))} /></div>
          <div className="w-40"><Select data-testid="news-impact" value={imp} onChange={(e) => setImp(e.target.value)} options={[{ value: "", label: "All impact" }, "CRITICAL", "HIGH", "MEDIUM", "LOW"]} /></div>
          <span className="ml-auto self-center font-mono text-[11px] text-slate-500">{data?.total ?? 0} headlines (deduplicated)</span>
        </div>
        {!data ? <Empty>Loading…</Empty> : data.items.map((x) => (
          <div key={x.id} className="py-2.5 border-b border-[#1e2a4a]/50" data-testid={`news-${x.id}`}>
            <div className="flex flex-wrap items-center gap-2"><Chip status={x.impact} /><Chip status={x.keyword_direction} label={x.keyword_direction} /><span className="font-mono text-[10.5px] text-slate-500">{x.publisher} · published {x.published_at ? `${ist(x.published_at)} (${ago(x.published_at)})` : "—"} · retrieved {ist(x.retrieved_at)}</span>
              {x.time_error && <Chip status="BLOCKED" label={x.time_error} />}<Chip status={x.review_status} /></div>
            <a href={x.link} target="_blank" rel="noreferrer" className="mt-1 block text-[14px] text-slate-100 hover:text-amber-300">{x.title}</a>
            <div className="mt-1 flex flex-wrap items-center gap-1.5">{x.categories.map((c) => <span key={c} className="rounded bg-[#0f172a] px-1.5 py-0.5 text-[10.5px] text-slate-400">{c}</span>)}
              {isAdmin && <span className="ml-auto flex gap-1"><Btn tone="teal" data-testid={`news-reviewed-${x.id}`} onClick={() => mark(x.id, "REVIEWED")}>Reviewed</Btn><Btn tone="ghost" data-testid={`news-ignore-${x.id}`} onClick={() => mark(x.id, "IGNORED")}>Not relevant</Btn></span>}</div>
          </div>
        ))}
        <div className="mt-3 flex items-center gap-2"><Btn tone="ghost" data-testid="news-prev" disabled={page <= 1} onClick={() => setPage(page - 1)}>Previous</Btn><span className="font-mono text-xs">page {page}</span><Btn tone="ghost" data-testid="news-next" disabled={!data || page * 25 >= data.total} onClick={() => setPage(page + 1)}>Next</Btn></div>
      </Card>
      <Events isAdmin={isAdmin} />
    </div>
  );
}
