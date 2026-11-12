import React, { useState } from "react";
import { toast } from "sonner";
import { useDesk } from "@/context/DeskContext";
import { useFetch } from "@/hooks/useFetch";
import { api, errText, API } from "@/lib/api";
import { Card, Chip, KV, PageHead, Empty, Note, Stat } from "@/components/desk/ui";
import { Field, Input, Select, Btn } from "@/components/desk/forms";
import { n, pct, ist, sgn } from "@/lib/fmt";
import { OutcomeBar, VIZ } from "@/components/desk/charts";
const OUTCOME_ORDER = ["TARGET FIRST", "STOP FIRST", "PENDING", "HISTORY GAP"];
const OUTCOME_COLORS = { "TARGET FIRST": VIZ.up, "STOP FIRST": VIZ.down, PENDING: VIZ.s1, "HISTORY GAP": "#475569" };

function Backtest({ bt }) {
  if (!bt?.computed_at) return <Empty>Rule backtest runs after the 30-day history sync (about every 5 minutes).</Empty>;
  return (
    <div className="grid gap-4 lg:grid-cols-2">
      {["scalp", "swing"].map((k) => { const b = bt[k]; if (!b) return null; return (
        <div key={k} className="rounded border border-[#1e2a4a] p-3" data-testid={`backtest-${k}`}>
          <div className="flex items-center justify-between"><span className="font-semibold">{k === "scalp" ? "Scalping rules (5m/15m)" : "Swing rules (1h/4h)"}</span><Chip status={b.expectancy_net_r > 0 ? "PASS" : b.expectancy_net_r == null ? "UNAVAILABLE" : "FAIL"} label={`exp ${sgn(b.expectancy_net_r, 2)} R`} /></div>
          <div className="grid grid-cols-2 gap-x-4 mt-2">
            <KV k="Signals" v={`${b.signals} (${b.long} long / ${b.short} short)`} /><KV k="Target first / stop first" v={`${b.target_first} / ${b.stop_first}`} />
            <KV k="Observed hit rate" v={pct(b.hit_rate, 1)} /><KV k="Breakeven hit rate" v={pct(b.breakeven_hit_rate, 1)} />
            <KV k="Same/capture-candle review" v={b.ambiguous} /><KV k="Pending / gap" v={b.pending_or_gap} />
          </div>
          <div className="mt-2 max-h-40 overflow-y-auto scroll-thin">{b.recent.map((t, i) => <div key={i} className="flex justify-between font-mono text-[10.5px] py-0.5"><span>{ist(t.time)}</span><span>{t.direction} {n(t.entry, 1)}</span><Chip status={t.outcome} /></div>)}</div>
        </div>); })}
      {bt.scalp?.market && bt.scalp?.limit && <ScalpModes b={bt.scalp} />}
      <p className="text-[11px] text-slate-500 lg:col-span-2">Historical simulation on {bt.bars} Delta BTCUSD completed 5m bars (higher frames aggregated from complete 5m groups), costs included, one position at a time. It cannot replay spread, news review, daily limits or quote age (F26/F30/F31/F33), so it is supporting evidence — not a guarantee. Computed {ist(bt.computed_at)}.</p>
    </div>
  );
}

function ScalpModes({ b }) {
  const rows = [["market", "Market entry + market exits"], ["limit", "Limit entry + limit target, stop-market"]];
  return (
    <div className="rounded border border-[#1e2a4a] p-3 lg:col-span-2" data-testid="backtest-scalp-modes">
      <div className="flex items-center justify-between"><span className="font-semibold">Scalp backtest — Market vs Limit (last 30 days)</span><span className="text-[11px] text-slate-400">active plan: <b className="text-amber-300">{b.mode === "limit" ? "LIMIT" : "MARKET"}</b></span></div>
      <div className="overflow-x-auto scroll-thin mt-2"><table className="desk-table"><thead><tr><th>Execution</th><th className="text-right">Trades</th><th className="text-right">Missed fills</th><th className="text-right">Hit target</th><th className="text-right">Breakeven</th><th className="text-right">Avg net R</th><th className="text-right">Total R</th><th className="text-right">Worst drawdown</th></tr></thead>
        <tbody>{rows.map(([k, label]) => { const x = b[k] || {}; return (
          <tr key={k} data-testid={`backtest-mode-${k}`} className={b.mode === k ? "bg-amber-500/5" : ""}><td>{label}</td><td className="text-right font-mono tabular-nums">{x.signals}</td><td className="text-right font-mono tabular-nums">{x.missed_fills}</td><td className="text-right font-mono tabular-nums">{pct(x.hit_rate, 1)}</td><td className="text-right font-mono tabular-nums">{pct(x.breakeven_hit_rate, 1)}</td><td className={`text-right font-mono tabular-nums ${x.expectancy_net_r > 0 ? "text-teal-300" : "text-red-300"}`}>{sgn(x.expectancy_net_r, 3)}</td><td className="text-right font-mono tabular-nums">{sgn(x.total_net_r, 1)}</td><td className="text-right font-mono tabular-nums">{n(x.max_drawdown_r, 1)} R</td></tr>); })}</tbody></table></div>
      <p className="mt-2 text-[11px] text-slate-500">{b.note} Small samples: treat as evidence, not a guarantee. The desk never places orders.</p>
    </div>
  );
}

function SnapForm({ snap, onDone }) {
  const { state } = useDesk();
  const mid = state?.quote?.bid ? (state.quote.bid + state.quote.ask) / 2 : "";
  const [f, setF] = useState(snap ? { direction: snap.direction, entry: snap.entry, stop: snap.stop, target: snap.target } : { direction: "LONG", entry: mid ? Math.round(mid) : "", stop: "", target: "" });
  const [err, setErr] = useState(null);
  const save = async () => {
    const body = { direction: f.direction, entry: Number(f.entry), stop: Number(f.stop), target: Number(f.target) };
    try { if (snap) await api.put(`/validation/${snap.id}`, { ...body, version: snap.version }); else await api.post("/validation", body); toast.success("Snapshot saved"); onDone(); } catch (e) { setErr(errText(e)); }
  };
  return (
    <div className="grid gap-2 sm:grid-cols-5 items-end" data-testid="snap-form">
      <Field label="Direction"><Select data-testid="snap-direction" value={f.direction} onChange={(e) => setF({ ...f, direction: e.target.value })} options={["LONG", "SHORT"]} /></Field>
      {["entry", "stop", "target"].map((k) => <Field key={k} label={`${k} (USD)`}><Input data-testid={`snap-${k}`} type="number" value={f[k]} onChange={(e) => setF({ ...f, [k]: e.target.value })} /></Field>)}
      <div className="flex gap-2"><Btn data-testid="snap-save" onClick={save}>{snap ? "Save (resets result)" : "Capture"}</Btn><Btn tone="ghost" onClick={onDone}>Cancel</Btn></div>
      {err && <div className="sm:col-span-5"><Note tone="red">{err} — draft kept.</Note></div>}
    </div>
  );
}

export default function Validation() {
  const { isAdmin } = useDesk();
  const [page, setPage] = useState(1);
  const [model, setModel] = useState("");
  const { data, reload } = useFetch(`/validation?page=${page}&size=25${model ? `&model=${model}` : ""}`, 30000);
  const [form, setForm] = useState(null);
  const h = data?.history;
  return (
    <div className="space-y-4">
      <PageHead title="Trade Validation" sub="Immutable direction snapshots validated against ~30 days of completed Delta BTCUSD 5m candles. First touch is frozen; a later target cannot reverse an earlier stop." />
      <div className="grid grid-cols-2 gap-3 md:grid-cols-5">
        <Stat label="5m history bars" value={h?.bars ?? "—"} sub={`expected ≈ ${h?.expected ?? "—"}`} testid="hist-bars" />
        <Stat label="Unexplained gaps" value={h?.gap_count ?? "—"} sub="after dedup" testid="hist-gaps" />
        <Stat label="History span" value={h?.first ? `${ist(h.first)}` : "—"} sub={`to ${ist(h?.last)}`} />
        <Stat label="Sync status" value={(h?.status || "—").toUpperCase()} sub={`${h?.pages ?? 0} pages · ${ist(h?.synced_at)}`} />
        <Stat label="Snapshots" value={data?.total ?? "—"} />
      </div>
      {h?.error && <Note tone="red">History source error: {h.error}</Note>}
      {h?.gaps?.length > 0 && <Note tone="amber">Gaps: {h.gaps.slice(0, 5).map((g) => `${ist(g.after)} → ${ist(g.before)} (${g.missing_bars} bars)`).join("; ")}</Note>}
      <Card title="Observed outcomes by model" testid="outcomes-by-model">
        {Object.keys(data?.outcomes_by_model || {}).length === 0 ? <Empty>No snapshots yet. The confirmed 15-minute model logs its first confirmed LONG/SHORT and each confirmed direction change; the original six-factor model logs only when complete.</Empty> :
          Object.entries(data.outcomes_by_model).map(([m, o]) => { const w = o["TARGET FIRST"] || 0, l = o["STOP FIRST"] || 0; return (
            <div key={m} className="border-b border-[#1e2a4a]/40 py-2.5"><div className="mb-1.5 flex flex-wrap items-center gap-2"><span className="font-mono text-sm text-amber-300">{m}</span><span className="ml-auto font-mono text-xs">{w + l ? `hit rate ${pct(w / (w + l), 0)} of ${w + l} resolved` : "no resolved outcomes yet"}</span></div>
              <OutcomeBar counts={o} order={[...OUTCOME_ORDER, ...Object.keys(o).filter((k) => !OUTCOME_ORDER.includes(k))]} colors={new Proxy(OUTCOME_COLORS, { get: (t, k) => t[k] || "#64748b" })} testid={`outcome-${m}`} /></div>); })}
      </Card>
      <Card title="Rule backtest evidence (30 days)" testid="backtest-card"><Backtest bt={data?.backtest} /></Card>
      <Card title="Snapshots" sub={data?.note} testid="snapshots-card" right={<div className="flex gap-2"><div className="w-48"><Select data-testid="snap-model-filter" value={model} onChange={(e) => { setModel(e.target.value); setPage(1); }} options={[{ value: "", label: "All models" }, { value: "FIVE_PILLAR_CONFIRMED_V2", label: "Confirmed 15m model (v2)" }, "FIVE_PILLAR_LIVE", "ORIGINAL_SIX_FACTOR", "MANUAL"]} /></div>{isAdmin && <Btn data-testid="snap-new" onClick={() => setForm("new")}>Manual snapshot</Btn>}{isAdmin && <a data-testid="export-validation" className="rounded border border-[#1e2a4a] px-3 py-1.5 text-xs" href={`${API}/export/validation`}>Export CSV</a>}</div>}>
        {form && <div className="mb-3 rounded border border-amber-500/30 p-3"><SnapForm snap={form === "new" ? null : form} onDone={() => { setForm(null); reload(); }} /></div>}
        {!data?.items?.length ? <Empty>No snapshots.</Empty> : (
          <div className="overflow-x-auto scroll-thin"><table className="desk-table"><thead><tr><th>ID</th><th>Model</th><th>Captured / quote</th><th>Dir</th><th>Score</th><th>Entry / Stop / Target</th><th>Stop frac · R</th><th>Outcome</th><th>Observed at</th><th /></tr></thead>
            <tbody>{data.items.map((x) => (
              <tr key={x.id}><td className="font-mono text-[10.5px]">{x.snapshot_id}</td><td className="font-mono text-[11px]">{x.model}</td><td className="font-mono text-[10.5px]">{ist(x.capture_time)}<br /><span className="text-slate-500">{ist(x.quote_time)}</span></td>
                <td><Chip status={x.direction} /></td><td className="font-mono">{sgn(x.score, 2)}</td><td className="font-mono">{n(x.entry, 1)} / {n(x.stop, 1)} / {n(x.target, 1)}</td><td className="font-mono">{n(x.stop_fraction, 3)} · {n(x.target_ratio, 2)}</td>
                <td><Chip status={x.outcome} />{x.frozen && <span className="ml-1 font-mono text-[10px] text-slate-500">frozen</span>}{x.edit_note && <div className="text-[10px] text-amber-400">{x.edit_note}</div>}</td><td className="font-mono text-[10.5px]">{ist(x.outcome_at)}</td>
                <td>{isAdmin && <Btn tone="ghost" data-testid={`snap-edit-${x.id}`} onClick={() => setForm(x)}>Edit</Btn>}</td></tr>
            ))}</tbody></table></div>
        )}
        <div className="mt-3 flex items-center gap-2"><Btn tone="ghost" disabled={page <= 1} onClick={() => setPage(page - 1)}>Previous</Btn><span className="font-mono text-xs">page {page}</span><Btn tone="ghost" disabled={!data || page * 25 >= data.total} onClick={() => setPage(page + 1)}>Next</Btn></div>
      </Card>
    </div>
  );
}
