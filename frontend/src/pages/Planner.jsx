import React, { useState } from "react";
import { api, errText } from "@/lib/api";
import { Card, Chip, KV, PageHead, Empty, Note } from "@/components/desk/ui";
import { Field, Input, Select, Btn } from "@/components/desk/forms";
import { n, usd, pct, sgn } from "@/lib/fmt";

export default function Planner() {
  const [f, setF] = useState({ direction: "LONG", entry_mode: "spot", limit_price: "", stop_fraction: "", target_r: "" });
  const [r, setR] = useState(null);
  const [err, setErr] = useState(null);
  const [busy, setBusy] = useState(false);
  const run = async () => {
    setBusy(true); setErr(null);
    const body = { direction: f.direction, entry_mode: f.entry_mode };
    ["limit_price", "stop_fraction", "target_r"].forEach((k) => { if (f[k] !== "") body[k] = Number(f[k]); });
    try { const { data } = await api.post("/planner", body); setR(data); } catch (e) { setErr(errText(e)); } finally { setBusy(false); }
  };
  const set = (k) => (e) => setF({ ...f, [k]: e.target.value });
  return (
    <div className="space-y-4">
      <PageHead title="Trade Planner" sub="Completed daily candles (Delta BTCUSD), cost-adjusted sizing, nine guardrails, nine ATR/risk scenarios, three-entry tranches and factor-flip sensitivity." />
      <Card title="Scenario inputs" testid="planner-form">
        <div className="grid gap-3 sm:grid-cols-3 lg:grid-cols-6 items-end">
          <Field label="Direction"><Select data-testid="planner-direction" value={f.direction} onChange={set("direction")} options={["LONG", "SHORT"]} /></Field>
          <Field label="Entry"><Select data-testid="planner-entry-mode" value={f.entry_mode} onChange={set("entry_mode")} options={[{ value: "spot", label: "Spot (live ask/bid)" }, { value: "limit", label: "Limit price" }]} /></Field>
          <Field label="Limit price (USD)"><Input data-testid="planner-limit" type="number" disabled={f.entry_mode !== "limit"} value={f.limit_price} onChange={set("limit_price")} /></Field>
          <Field label="Stop fraction" hint="blank = swing default"><Input data-testid="planner-stop" type="number" step="0.001" value={f.stop_fraction} onChange={set("stop_fraction")} /></Field>
          <Field label="Target R" hint="blank = swing default"><Input data-testid="planner-target" type="number" step="0.1" value={f.target_r} onChange={set("target_r")} /></Field>
          <Btn data-testid="planner-run" onClick={run} disabled={busy}>{busy ? "Calculating…" : "Calculate plan"}</Btn>
        </div>
        {err && <div className="mt-3"><Note tone="red">{err}</Note></div>}
      </Card>
      {!r ? <Empty>Choose a scenario and press Calculate plan.</Empty> : r.error ? <Note tone="red">{r.error}</Note> : (
        <>
          <div className="grid gap-4 lg:grid-cols-3">
            <Card title="Daily context" sub="Completed 1d candles" testid="planner-daily">
              <KV k="Close" v={n(r.daily.close, 1)} /><KV k="EMA20 / EMA50" v={`${n(r.daily.ema20, 1)} / ${n(r.daily.ema50, 1)}`} />
              <KV k="ATR14" v={n(r.daily.atr14, 1)} /><KV k="Wilder RSI14" v={n(r.daily.rsi14, 1)} /><KV k="Bars" v={r.daily.bars} />
            </Card>
            <Card title={`Base plan — ${r.direction} @ ${n(r.entry, 1)}`} sub={`${r.entry_mode} entry · hold ${r.hold_hours}h`} testid="planner-base">
              <KV k="Stop / target" v={`${n(r.base.stop, 1)} / ${n(r.base.target, 1)}`} /><KV k="Quantity" v={`${n(r.base.qty_btc, 3)} BTC · ${usd(r.base.notional, 0)}`} testid="planner-qty" />
              <KV k="Net R (cost-adjusted)" v={n(r.base.net_r, 3)} testid="planner-netr" /><KV k="Risk / reward" v={`${usd(r.base.risk_usd)} / ${usd(r.base.reward_usd)}`} />
              <KV k="Breakeven hit rate" v={pct(r.base.breakeven_fraction, 1)} /><KV k={`Assumed expectancy (win rate ${r.win_rate_assumed})`} v={`${sgn(r.base.assumed_expectancy_r, 3)} R`} />
              <p className="mt-2 text-[10.5px] text-slate-500">{r.note}</p>
            </Card>
            <Card title="Nine guardrails" testid="planner-guardrails">
              {r.guardrails.map((g) => <div key={g.name} className="flex items-center justify-between py-1 border-b border-[#1e2a4a]/40 text-xs"><span>{g.name}</span><Chip status={g.pass ? "PASS" : "FAIL"} /></div>)}
            </Card>
          </div>
          <Card title="Nine ATR / risk scenarios" testid="planner-scenarios">
            <div className="overflow-x-auto scroll-thin"><table className="desk-table"><thead><tr><th>Stop (×ATR)</th><th>Target R</th><th>Stop</th><th>Target</th><th>Qty BTC</th><th>Net R</th><th>Risk</th><th>Reward</th><th>Breakeven</th><th>Assumed exp.</th></tr></thead>
              <tbody>{r.scenarios.map((x, i) => <tr key={i}><td className="font-mono">{x.stop_atr}</td><td className="font-mono">{x.target_r}</td><td className="font-mono">{n(x.stop, 1)}</td><td className="font-mono">{n(x.target, 1)}</td><td className="font-mono">{n(x.qty_btc, 3)}</td><td className={`font-mono ${x.net_r > 1 ? "text-teal-300" : "text-red-300"}`}>{n(x.net_r, 2)}</td><td className="font-mono">{usd(x.risk_usd, 0)}</td><td className="font-mono">{usd(x.reward_usd, 0)}</td><td className="font-mono">{pct(x.breakeven_fraction, 1)}</td><td className="font-mono">{sgn(x.assumed_expectancy_r, 2)}</td></tr>)}</tbody></table></div>
          </Card>
          <div className="grid gap-4 lg:grid-cols-2">
            <Card title="Three-entry tranches" sub={r.tranches.note} testid="planner-tranches">
              {r.tranches.rows.map((t, i) => <KV key={i} k={`Tranche ${i + 1}: ${pct(t.fraction, 0)} at ${t.atr_offset}×ATR`} v={`${n(t.price, 1)} · ${n(t.qty_btc, 3)} BTC`} />)}
              <KV k="Average entry / total" v={`${n(r.tranches.avg_entry, 1)} · ${n(r.tranches.total_qty, 3)} BTC`} />
            </Card>
            <Card title="Factor-flip sensitivity" sub={`Live five-pillar base ${sgn(r.factor_flip.base, 3)} (${r.factor_flip.base_direction})`} testid="planner-flip">
              {r.factor_flip.rows.length ? r.factor_flip.rows.map((x) => <KV key={x.factor} k={`Flip ${x.factor} ${sgn(x.from, 0)}→${sgn(x.to, 0)}`} v={<span>{sgn(x.new_score, 3)} <Chip status={x.new_direction} /></span>} />) : <Empty>No non-zero valid pillars to flip.</Empty>}
            </Card>
          </div>
        </>
      )}
    </div>
  );
}
