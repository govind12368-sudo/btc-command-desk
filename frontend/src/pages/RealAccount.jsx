import React, { useState } from "react";
import { toast } from "sonner";
import { api, errText } from "@/lib/api";
import { useFetch } from "@/hooks/useFetch";
import { Card, PageHead, Note, KpiStrip, Empty, More } from "@/components/desk/ui";
import { Field, Input, Select, Btn } from "@/components/desk/forms";
import { n, signedUsd } from "@/lib/fmt";

const tone = (v) => (v > 0 ? "text-teal-300" : v < 0 ? "text-red-300" : "text-slate-300");
const usd = (v) => (v == null ? "—" : `${v < 0 ? "−" : ""}$${n(Math.abs(v), 2)}`);
const RES_TONE = { Target: "text-teal-300", Stop: "text-red-300" };
const rr = (v) => (v == null ? "—" : `1 : ${v.toFixed(2)}`);
const EMPTY = { entry_ist: "", side: "LONG", entry: "", sl: "", tp: "", result: "Target", exit: "" };

function AddTrade({ results, onSaved }) {
  const [v, setV] = useState(EMPTY);
  const [busy, setBusy] = useState(false);
  const F = (k, label, hint) => (
    <Field label={label} hint={hint}>
      <Input type={k === "entry_ist" ? "text" : "number"} placeholder={k === "entry_ist" ? "2026-11-25 17:30" : ""} value={v[k]}
        onChange={(e) => setV({ ...v, [k]: e.target.value })} data-testid={`real-add-${k}`} />
    </Field>
  );
  const save = async () => {
    setBusy(true);
    try {
      const body = { ...v, entry: +v.entry, sl: +v.sl, tp: +v.tp, exit: +v.exit };
      const r = await api.post("/real-account/trades", body);
      onSaved(r.data);
      setV(EMPTY);
      toast.success("Trade added");
    } catch (e) { toast.error(errText(e)); } finally { setBusy(false); }
  };
  return (
    <More label="Add a real trade" testid="real-add">
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4 lg:grid-cols-7">
        {F("entry_ist", "Entry (IST)", "YYYY-MM-DD HH:MM")}
        <Field label="Rev side"><Select value={v.side} onChange={(e) => setV({ ...v, side: e.target.value })} options={["LONG", "SHORT"]} data-testid="real-add-side" /></Field>
        {F("entry", "Entry")}
        {F("sl", "Rev SL")}
        {F("tp", "Rev TP")}
        <Field label="Result"><Select value={v.result} onChange={(e) => setV({ ...v, result: e.target.value })} options={results} data-testid="real-add-result" /></Field>
        {F("exit", "Rev exit", "the real fill price")}
      </div>
      <div className="mt-3 flex items-center gap-2">
        <Btn disabled={busy} onClick={save} data-testid="real-add-save">{busy ? "Adding…" : "Add trade"}</Btn>
        <span className="text-[11.5px] text-slate-500">Balance, position, size, P&L and fees are worked out from these prices.</span>
      </div>
    </More>
  );
}

export default function RealAccount() {
  const { data: fetched, error } = useFetch("/real-account");
  const [saved, setSaved] = useState(null);
  const [ask, setAsk] = useState(null);
  const d = saved || fetched;
  const remove = async (id) => {
    if (ask !== id) { setAsk(id); return; }
    try { const r = await api.delete(`/real-account/trades/${encodeURIComponent(id)}`); setSaved(r.data); setAsk(null); toast.success("Trade removed"); }
    catch (e) { toast.error(errText(e)); }
  };
  if (!d) return <div className="space-y-4"><PageHead title="Real Account Tested" />{error ? <Note tone="red">Could not load the real account trades. {error}</Note> : <Empty>Loading…</Empty>}</div>;
  const t = d.totals, rows = d.rows || [];
  return (
    <div className="space-y-4" data-testid="real-account-page">
      <PageHead title="Real Account Tested"
        sub={`Real trades on Delta Exchange, entered by hand${t.first ? ` · ${t.first} → ${t.last} IST` : ""}. Admin only.`} />
      <Note tone="plain">
        These are trades from your real Delta Exchange account. <b>Rev</b> = reversed signal: the side, stop and target are swapped against the original signal,
        so the target is closer than the stop (Reward:Risk below 1). Start balance ${n(d.start_bal, 0)}; each trade uses {n(d.leverage, 0)}× the balance before it;
        fees are Delta's {n(d.fee_pct, 2)}% taker fee on entry and exit + {n(d.gst_pct, 0)}% GST.
      </Note>
      <KpiStrip testid="real-kpi" items={[
        { label: "Trades", value: t.trades, sub: `${t.wins} won · ${t.losses} lost` },
        { label: "Win ratio", value: t.win_pct == null ? "—" : `${n(t.win_pct, 0)}%`, sub: "net after fees > 0" },
        { label: "Gross P&L", value: signedUsd(t.gross), cls: tone(t.gross), sub: "before fees" },
        { label: "Fees", value: usd(-t.fees), sub: "taker + GST" },
        { label: "Net P&L", value: signedUsd(t.net), cls: tone(t.net), sub: "after fees" },
        { label: "Balance", value: usd(t.end_bal), cls: tone(t.net), sub: `from ${usd(t.start_bal)} · ${t.return_pct >= 0 ? "+" : "−"}${n(Math.abs(t.return_pct), 1)}%` },
      ]} />
      <Card title="Journal and P&L" sub="Oldest first. Each start balance is the end balance of the trade before." testid="real-journal" pad={false}>
        {rows.length ? (
          <div className="overflow-x-auto scroll-thin">
            <table className="desk-table" data-testid="real-table">
              <thead><tr>
                <th>Entry (IST)</th><th>Rev side</th><th className="num">Entry</th><th className="num">Rev SL</th><th className="num">Rev TP</th><th className="num">Reward:Risk</th>
                <th>Result</th><th className="num">Rev exit</th><th className="num">Start bal ($)</th><th className="num">Position ($)</th><th className="num">Qty (BTC)</th>
                <th className="num">Gross ($)</th><th className="num">Fees ($)</th><th className="num">Net ($)</th><th className="num">End bal ($)</th><th />
              </tr></thead>
              <tbody>
                {rows.map((r) => (
                  <tr key={r.id} data-testid="real-row">
                    <td className="font-mono">{r.entry_ist}</td>
                    <td className={`font-semibold ${r.side === "LONG" ? "text-teal-300" : "text-red-300"}`}>{r.side}</td>
                    <td className="num">{n(r.entry, 1)}</td><td className="num">{n(r.sl, 1)}</td><td className="num">{n(r.tp, 1)}</td><td className="num">{rr(r.rr)}</td>
                    <td className={`font-semibold ${RES_TONE[r.result] || "text-slate-300"}`}>{r.result}</td>
                    <td className="num">{n(r.exit, 1)}</td><td className="num">{usd(r.start_bal)}</td><td className="num">{usd(r.position)}</td><td className="num">{n(r.qty, 4)}</td>
                    <td className={`num ${tone(r.gross)}`}>{signedUsd(r.gross)}</td><td className="num text-slate-300">{usd(-r.fees)}</td>
                    <td className={`num font-semibold ${tone(r.net)}`}>{signedUsd(r.net)}</td><td className="num">{usd(r.end_bal)}</td>
                    <td><Btn tone={ask === r.id ? "red" : "ghost"} className="!px-2 !py-0.5 !text-[11px]" onClick={() => remove(r.id)} onBlur={() => setAsk(null)} data-testid="real-remove">{ask === r.id ? "Confirm remove" : "Remove"}</Btn></td>
                  </tr>
                ))}
                <tr className="font-semibold" data-testid="real-total">
                  <td colSpan={11} className="text-slate-300">Total</td>
                  <td className={`num ${tone(t.gross)}`}>{signedUsd(t.gross)}</td><td className="num text-slate-300">{usd(-t.fees)}</td>
                  <td className={`num ${tone(t.net)}`}>{signedUsd(t.net)}</td><td className="num">{usd(t.end_bal)}</td><td />
                </tr>
              </tbody>
            </table>
          </div>
        ) : <div className="p-3"><Empty>No real trades yet. Add one below.</Empty></div>}
      </Card>
      <AddTrade results={d.results || ["Target", "Stop"]} onSaved={setSaved} />
    </div>
  );
}
