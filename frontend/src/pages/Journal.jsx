import React, { useState } from "react";
import { toast } from "sonner";
import { LineChart, Line, XAxis, YAxis, Tooltip, ResponsiveContainer } from "recharts";
import { useDesk } from "@/context/DeskContext";
import { useFetch } from "@/hooks/useFetch";
import { BookPanel, DayChip, DayTypeCard, PeriodSwitch, PLAYBOOK_NAME } from "@/components/desk/Books";
import { api, errText, API } from "@/lib/api";
import { Card, Chip, KV, PageHead, Empty, Note, Stat } from "@/components/desk/ui";
import { Field, Input, Select, Btn, Area } from "@/components/desk/forms";
import { n, usd, pct, ist, sgn, tone } from "@/lib/fmt";
import { DeltaSyncCard } from "@/components/desk/Alerts";

const BLANK = { strategy: "scalp", direction: "LONG", entry_time: "", entry_price: "", qty_btc: "", exit_time: "", exit_price: "", fees_usd: "", funding_usd: "", stop_price: "", exchange_id: "", plan_id: "", notes: "" };
const toLocal = (iso) => (iso ? new Date(new Date(iso).getTime() + 5.5 * 3600e3).toISOString().slice(0, 16) : "");
const fromLocalIST = (v) => (v ? `${v}:00+05:30` : null);

function FillForm({ editing, onDone }) {
  const [f, setF] = useState(() => (editing ? Object.fromEntries(Object.keys(BLANK).map((k) => [k, k.endsWith("_time") ? toLocal(editing[k]) : editing[k] ?? ""])) : BLANK));
  const [err, setErr] = useState(null);
  const set = (k) => (e) => setF({ ...f, [k]: e.target.value });
  const save = async () => {
    setErr(null);
    const num = (v) => (v === "" || v === null ? null : Number(v));
    const body = { ...f, entry_time: fromLocalIST(f.entry_time), exit_time: fromLocalIST(f.exit_time), entry_price: num(f.entry_price), qty_btc: num(f.qty_btc), exit_price: num(f.exit_price),
      fees_usd: num(f.fees_usd), funding_usd: num(f.funding_usd), stop_price: num(f.stop_price), exchange_id: f.exchange_id || null, plan_id: f.plan_id || null };
    try {
      if (editing) await api.put(`/fills/${editing.id}`, { ...body, version: editing.version }); else await api.post("/fills", body);
      toast.success("Saved"); onDone();
    } catch (e) { setErr(errText(e)); }
  };
  const F = (k, label, type = "number", hint) => <Field label={label} hint={hint}><Input data-testid={`fill-${k}`} type={type} step="any" value={f[k]} onChange={set(k)} /></Field>;
  return (
    <div data-testid="fill-form">
      <div className="grid gap-3 sm:grid-cols-3 lg:grid-cols-6">
        <Field label="Strategy"><Select data-testid="fill-strategy" value={f.strategy} onChange={set("strategy")} options={["scalp", "swing", "other"]} /></Field>
        <Field label="Direction"><Select data-testid="fill-direction" value={f.direction} onChange={set("direction")} options={["LONG", "SHORT"]} /></Field>
        {F("entry_time", "Entry time (IST)", "datetime-local")}{F("entry_price", "Actual entry (USD)")}{F("qty_btc", "Quantity (BTC)", "number", "contracts × 0.001")}{F("stop_price", "Planned stop (for R)")}
        {F("exit_time", "Exit time (IST)", "datetime-local")}{F("exit_price", "Actual exit (USD)")}{F("fees_usd", "Actual fees (USD)", "number", "blank = unknown")}{F("funding_usd", "Signed funding (USD)", "number", "paid +, received −")}
        {F("exchange_id", "Exchange fill ID", "text")}{F("plan_id", "Swing plan ID", "text", "consumes the plan")}
      </div>
      <div className="mt-3"><Field label="Notes"><Area data-testid="fill-notes" value={f.notes} onChange={set("notes")} /></Field></div>
      {err && <div className="mt-2"><Note tone="red">Not saved: {err}. Your draft is kept.</Note></div>}
      <div className="mt-3 flex gap-2"><Btn data-testid="fill-save" onClick={save}>{editing ? "Save changes" : "Add actual fill"}</Btn><Btn tone="ghost" data-testid="fill-cancel" onClick={onDone}>Cancel</Btn></div>
    </div>
  );
}

function Actual({ isAdmin }) {
  const { data, reload } = useFetch("/fills", 10000);
  const [mode, setMode] = useState(null);
  const [csv, setCsv] = useState("");
  const imp = async () => {
    try { const { data: r } = await api.post("/fills/import", { csv_text: csv }); toast.success(`Imported ${r.added}, duplicates skipped ${r.duplicates_skipped}`); if (r.errors.length) toast.error(r.errors.slice(0, 3).join(" | ")); setCsv(""); reload(); } catch (e) { toast.error(errText(e)); }
  };
  const del = async (id) => { if (!window.confirm("Move this record to the fills archive?")) return; try { await api.delete(`/fills/${id}`); reload(); } catch (e) { toast.error(errText(e)); } };
  const st = data?.stats;
  return (
    <div className="space-y-4">
      <DeltaSyncCard isAdmin={isAdmin} onDone={reload} />
      {data?.capacity?.notice && <Note tone="amber">{data.capacity.notice}</Note>}
      <div className="grid grid-cols-2 gap-3 md:grid-cols-4 xl:grid-cols-8">
        <Stat label="Equity" value={usd(st?.equity, 0)} testid="stat-equity" /><Stat label="Net P&L" value={usd(st?.net_pnl, 0)} valueClass={tone(st?.net_pnl)} />
        <Stat label="Max drawdown" value={usd(st?.max_drawdown, 0)} /><Stat label="Observed win rate" value={pct(st?.win_rate, 0)} />
        <Stat label="Expectancy / trade" value={usd(st?.expectancy_usd, 0)} /><Stat label="Profit factor" value={n(st?.profit_factor, 2)} />
        <Stat label="Net R total" value={n(st?.net_r_total, 2)} /><Stat label="Unknown-cost trades" value={st?.unknown_cost_trades ?? "—"} />
      </div>
      {st?.curve?.length > 1 && <Card title="Equity curve (actual fills, known costs)"><div className="h-48"><ResponsiveContainer><LineChart data={st.curve}><XAxis dataKey="time" hide /><YAxis domain={["auto", "auto"]} stroke="#64748b" fontSize={10} /><Tooltip contentStyle={{ background: "#131c35", border: "1px solid #1e2a4a" }} /><Line dataKey="equity" stroke="#f59e0b" dot={false} /></LineChart></ResponsiveContainer></div></Card>}
      <Card title="Actual exchange fills" sub={`${st?.note || ""} Live unrealized P&L uses Delta mid ${n(data?.mark, 1)}.`} testid="fills-card"
        right={<div className="flex gap-2">{isAdmin && <Btn data-testid="fill-add" onClick={() => setMode("new")}>Add fill</Btn>}<a data-testid="export-fills" className="rounded border border-[#1e2a4a] px-3 py-1.5 text-xs hover:border-amber-500/50" href={`${API}/export/fills`}>Export CSV</a></div>}>
        {mode && <div className="mb-4 rounded border border-amber-500/30 p-3"><FillForm key={mode === "new" ? "new" : mode.id} editing={mode === "new" ? null : mode} onDone={() => { setMode(null); reload(); }} /></div>}
        {!data?.items?.length ? <Empty>No actual fills recorded. Candle touches never become fills — add your real Delta executions here.</Empty> : (
          <div className="overflow-x-auto scroll-thin"><table className="desk-table"><thead><tr><th>Entry (IST)</th><th>Strategy</th><th>Dir</th><th>Entry</th><th>Qty BTC</th><th>Exit</th><th>Gross</th><th>Fees</th><th>Funding</th><th>Net</th><th>R</th><th>Unrealized</th><th>Exch ID</th><th /></tr></thead>
            <tbody>{data.items.map((x) => (
              <tr key={x.id}><td className="font-mono text-[11px]">{ist(x.entry_time)}</td><td>{x.strategy}</td><td><Chip status={x.direction} /></td><td className="font-mono">{n(x.entry_price, 1)}</td><td className="font-mono">{x.qty_btc}</td>
                <td className="font-mono">{x.exit_price ? n(x.exit_price, 1) : <Chip status="MONITOR" label="OPEN" />}</td><td className={`font-mono ${tone(x.pnl.gross)}`}>{usd(x.pnl.gross)}</td>
                <td className="font-mono">{x.fees_usd ?? "unknown"}</td><td className="font-mono">{x.funding_usd ?? "unknown"}</td><td className={`font-mono ${tone(x.pnl.net)}`}>{x.pnl.net_known ? usd(x.pnl.net) : x.exit_price ? "unknown" : "—"}</td>
                <td className="font-mono">{n(x.pnl.r_multiple, 2)}</td><td className={`font-mono ${tone(x.pnl.unrealized)}`}>{usd(x.pnl.unrealized)}</td><td className="font-mono text-[11px]">{x.exchange_id || "—"}</td>
                <td>{isAdmin && <div className="flex gap-1"><Btn tone="ghost" data-testid={`fill-edit-${x.id}`} onClick={() => setMode(x)}>Edit</Btn><Btn tone="red" data-testid={`fill-del-${x.id}`} onClick={() => del(x.id)}>Archive</Btn></div>}</td></tr>
            ))}</tbody></table></div>
        )}
        {st?.by_strategy && Object.keys(st.by_strategy).length > 0 && <div className="mt-3 flex flex-wrap gap-4 font-mono text-[11px] text-slate-400">{Object.entries(st.by_strategy).map(([k, v]) => <span key={k}>{k}: {v.trades} trades, {v.wins} wins, net {usd(v.net)}</span>)}</div>}
      </Card>
      {isAdmin && <Card title="CSV import" sub="Columns: exchange_id,strategy,direction,entry_time,entry_price,qty_btc,exit_time,exit_price,fees_usd,funding_usd,stop_price,notes (times ISO 8601 with timezone). Duplicate exchange IDs are skipped." testid="csv-import">
        <Area data-testid="csv-text" value={csv} onChange={(e) => setCsv(e.target.value)} placeholder="exchange_id,strategy,direction,entry_time,entry_price,qty_btc,..." />
        <Btn className="mt-2" data-testid="csv-import-btn" disabled={!csv.trim()} onClick={imp}>Import CSV</Btn>
      </Card>}
    </div>
  );
}

function Signals({ isAdmin }) {
  const [page, setPage] = useState(1);
  const { data } = useFetch(`/signals?page=${page}&size=25`, 15000);
  const [open, setOpen] = useState(null);
  return (
    <Card title="Automatic model-signal journal" sub={data?.note} testid="signals-card" right={isAdmin && <a data-testid="export-signals" className="rounded border border-[#1e2a4a] px-3 py-1.5 text-xs hover:border-amber-500/50" href={`${API}/export/signals`}>Export CSV</a>}>
      {!data?.items?.length ? <Empty>No signal has passed all 12 checks yet. Captures are deduplicated by strategy, direction and confirmation bar.</Empty> : (
        <div className="overflow-x-auto scroll-thin"><table className="desk-table"><thead><tr><th>Signal ID</th><th>Captured</th><th>Dir</th><th>Day type at signal</th><th>Result at 1:2 (6h rule)</th><th>Entry / Stop / Target</th><th>Qty</th><th>Net R</th><th>Fill</th><th>Closed-candle observation</th><th /></tr></thead>
          <tbody>{data.items.map((x) => (<React.Fragment key={x.id}>
            <tr><td className="font-mono text-[11px]">{x.signal_id}</td><td className="font-mono text-[11px]">{ist(x.captured_at)}</td><td><Chip status={x.direction} /></td>
              <td><DayChip type={x.day_type} /><span className="block text-[10.5px] text-slate-500">{PLAYBOOK_NAME[x.playbook] || ""}</span></td>
              <td className="font-mono text-[11.5px]">{x.follow_result ? <span className={x.follow_net >= 0 ? "text-teal-300" : "text-red-300"}>{x.follow_result} {x.follow_net >= 0 ? "+" : "−"}${n(Math.abs(x.follow_net), 2)}</span> : "—"}</td>
              <td className="font-mono">{n(x.entry, 1)} / {n(x.stop, 1)} / {n(x.follow_target ?? x.target, 1)}{x.follow_target != null && Math.abs(x.follow_target - x.target) > 0.6 && <span className="block text-[10px] text-slate-500" title="Target the signal was captured with (1.5R, before 14 Nov)">signal target {n(x.target, 1)}</span>}</td><td className="font-mono">{x.qty_btc}</td><td className="font-mono">{n(x.net_r, 2)}</td>
              <td title={x.entry_type || "MARKET"}><Chip status={x.fill_status === "MISSED" ? "FAIL" : x.fill_status === "WAITING FILL" ? "PENDING" : "PASS"} label={x.fill_status || "FILLED"} /><span className="block font-mono text-[10px] text-slate-500">{x.entry_type === "POST-ONLY LIMIT" ? "limit" : "market"}{x.filled_at ? ` · ${ist(x.filled_at)}` : ""}</span></td>
              <td><Chip status={x.observation} /> <span className="font-mono text-[10px] text-slate-500">{x.observed_at ? ist(x.observed_at) : ""}</span></td><td><Btn tone="ghost" onClick={() => setOpen(open === x.id ? null : x.id)}>Checks</Btn></td></tr>
            {open === x.id && <tr><td colSpan={11}><div className="grid sm:grid-cols-2 gap-1 font-mono text-[11px]">{x.checks.map((c) => <span key={c.id}>{c.id} {c.status} — {c.detail}</span>)}</div><div className="mt-1 font-mono text-[10.5px] text-slate-500">quote {x.quote_source} {ist(x.quote_time)} · candles retrieved {ist(x.candle_retrieved)}</div></td></tr>}
          </React.Fragment>))}</tbody></table></div>
      )}
      <div className="mt-3 flex items-center gap-2"><Btn tone="ghost" disabled={page <= 1} onClick={() => setPage(page - 1)}>Previous</Btn><span className="font-mono text-xs">page {page} · {data?.total ?? 0} total</span><Btn tone="ghost" disabled={!data || page * 25 >= data.total} onClick={() => setPage(page + 1)}>Next</Btn></div>
    </Card>
  );
}

function TopPanel() {
  const { data } = useFetch("/plan-a", 60000);
  const [period, setPeriod] = useState("since");
  const b = data?.books?.[period] || {};
  const money = (v) => (v == null ? "—" : `${v >= 0 ? "+" : "−"}$${n(Math.abs(v), 2)}`);
  return (
    <div className="space-y-3" data-testid="journal-top">
      <Card title="Scalp signals traded in their own direction, 1:2" sub="Each signal's own direction, entry and stop; target = 2 × stop distance for every signal (signals before 14 Nov were captured with 1.5×). Same rules as Plan A: 10-minute entry, one trade at a time, 6-hour exit, same fees."
        right={<PeriodSwitch value={period} onChange={setPeriod} sinceDate={data?.books?.since_date} />}>
        <BookPanel book={b.follow} sub="Closed trades, after estimated fees" testid="journal-book" />
        <p className="mt-3 text-[12.5px] text-slate-300">Same signals, same period: Plan A (reverse) <b className={b.plan_a?.net >= 0 ? "text-teal-300" : "text-red-300"}>{money(b.plan_a?.net)}</b> on {b.plan_a?.taken ?? 0} trades · Day-type switch <b className={b.switch?.net >= 0 ? "text-teal-300" : "text-red-300"}>{money(b.switch?.net)}</b> on {b.switch?.taken ?? 0} trades.</p>
      </Card>
      <DayTypeCard day={data?.day_type} compact />
    </div>
  );
}

export default function Journal() {
  const { isAdmin } = useDesk();
  const [tab, setTab] = useState("auto");
  return (
    <div className="space-y-4">
      <PageHead title="Trade Journal" sub="A. automatic model signals and B. actual exchange fills are kept strictly separate. This app never places orders." />
      <div className="flex gap-2">
        {[["auto", "A. Model signals"], ...(isAdmin ? [["actual", "B. Actual fills"]] : [])].map(([k, l]) => <button key={k} data-testid={`tab-${k}`} onClick={() => setTab(k)} className={`rounded px-4 py-2 text-sm border transition-colors ${tab === k ? "border-amber-500/60 bg-amber-500/10 text-amber-300" : "border-[#1e2a4a] text-slate-400"}`}>{l}</button>)}
      </div>
      {(tab === "auto" || !isAdmin) && <TopPanel />}
      {tab === "auto" || !isAdmin ? <Signals isAdmin={isAdmin} /> : <Actual isAdmin={isAdmin} />}
    </div>
  );
}
