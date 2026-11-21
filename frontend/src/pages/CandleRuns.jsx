import React, { useState } from "react";
import { useFetch } from "@/hooks/useFetch";
import { Card, PageHead, Note } from "@/components/desk/ui";
import { n, signedUsd, ist } from "@/lib/fmt";
import { LineChart, VIZ } from "@/components/desk/charts";

const pnlTone = (v) => (v > 0 ? "text-teal-300" : v < 0 ? "text-red-300" : "text-slate-300");
const pctTxt = (v) => (v == null ? "—" : `${Math.round(v * 100)}%`);
const RESULT_TONE = { Target: "text-teal-300", Stop: "text-red-300", "Time exit": "text-amber-300", Open: "text-sky-300" };
const VERDICT_TONE = { PASS: "bg-teal-500/15 text-teal-300 border-teal-500/40", WEAK: "bg-amber-500/15 text-amber-300 border-amber-500/40", FAIL: "bg-red-500/15 text-red-300 border-red-500/40" };

function Stat({ label, value, tone = "text-slate-50", sub }) {
  return (
    <div className="rounded-lg border border-[#1e2a4a] bg-[#0f172a]/60 px-3 py-2.5">
      <div className="text-[11.5px] text-slate-400">{label}</div>
      <div className={`font-mono text-lg font-bold ${tone}`}>{value}</div>
      {sub && <div className="text-[11px] text-slate-500">{sub}</div>}
    </div>
  );
}

function Totals({ s }) {
  if (!s) return null;
  return (
    <div className="grid grid-cols-2 gap-2 sm:grid-cols-4 lg:grid-cols-7" data-testid="runs-totals">
      <Stat label="Total trades" value={s.trades} sub={`${s.targets} target · ${s.stops} stop · ${s.time_exits} time`} />
      <Stat label="Closed" value={s.closed} sub={`${s.wins} won · ${s.losses} lost`} />
      <Stat label="Open" value={s.open} tone="text-sky-300" sub={s.open ? `open P&L ${signedUsd(s.open_pnl)}` : null} />
      <Stat label="Win ratio" value={pctTxt(s.win_rate)} sub={s.closed ? `${s.wins} of ${s.closed} closed` : "no closed trades yet"} />
      <Stat label="Gross P&L (before fees)" value={signedUsd(s.gross)} tone={pnlTone(s.gross)} sub="closed trades" />
      <Stat label="Net P&L (after fees)" value={signedUsd(s.net)} tone={pnlTone(s.net)} sub={`fees ${signedUsd(-s.fees)}`} />
      <Stat label="Cumulative profit" value={signedUsd(s.cumulative)} tone={pnlTone(s.cumulative)} sub="net closed + open at live price" />
    </div>
  );
}

function Detail({ r }) {
  const kv = [["Signal (candle close)", r.signal_ist], ["Entry filled", `${r.entry_ist} at ${n(r.entry, 1)}`], ["Market (30 days)", r.chg30 == null ? "—" : `${r.market} · ${(r.chg30 * 100).toFixed(1)}%`],
    ["Stop distance (2 x ATR)", `$${n(r.d, 1)}`], ["Risk : reward", `1:${r.rr}`], ["Size", `${r.size} BTC ($10,000)`],
    ["Time exit at", r.time_exit_ist], ["Exit", r.exit_ist ? `${r.exit_ist} at ${n(r.exit_price, 1)}` : r.mark ? `open, marked at ${n(r.mark, 1)}` : "—"],
    ["Gross / fees", `${signedUsd(r.gross)} / ${signedUsd(-r.fees)}`], ["Result in R", r.r == null ? "—" : `${r.r > 0 ? "+" : ""}${r.r.toFixed(2)}R`],
    ["Held", r.hold_h == null ? "—" : `${r.hold_h} h`], ["Strategy running total", signedUsd(r.strategy_running)]];
  return (
    <div className="grid gap-x-6 gap-y-1 sm:grid-cols-2 lg:grid-cols-3">
      {kv.map(([k, v]) => <div key={k} className="flex justify-between gap-3 border-b border-[#1e2a4a]/60 py-1"><span className="text-slate-400">{k}</span><span className="font-mono text-slate-200">{v}</span></div>)}
      <div className="pt-1 text-slate-300 sm:col-span-2 lg:col-span-3">Why: {r.reason}</div>
    </div>
  );
}

export default function CandleRuns() {
  const { data } = useFetch("/candle-runs", 60000);
  const [period, setPeriod] = useState("live");
  const [only, setOnly] = useState(null);
  const [open, setOpen] = useState(null);
  const d = data || {};
  const strats = d.strategies || [];
  const rowsP = (d.rows || []).filter((r) => period === "all" || r.period === "Live");
  const rows = only ? rowsP.filter((r) => r.strategy === only) : rowsP;
  const tot = only ? strats.find((s) => s.key === only)?.[period] : d.totals?.[period];
  let eq = 0;
  const curve = [...rows].reverse().filter((r) => r.status === "CLOSED").map((r) => ({ t: r.exit_ist, v: (eq += r.net) }));
  const btn = (on) => `rounded px-3 py-1.5 text-xs font-semibold ${on ? "bg-amber-500 text-slate-950" : "border border-[#1e2a4a] text-slate-300 hover:text-white"}`;
  const mk = d.market_now;
  const upOnly = strats.filter((s) => s.market === "Up");
  return (
    <div className="space-y-4" data-testid="runs-page">
      <PageHead title="Candle Runs – 5 paper strategies" sub={`Paper trades only: no real orders and no alerts. From your backtest workbook, re-tested. Updated ${ist(d.computed_at)} · BTC ${n(d.live_price, 1)}${d.last_candle_ist ? ` · last 5m candle ${d.last_candle_ist}` : ""}`} />
      {d.error && <Note tone="red">Last update failed: {d.error}. It retries every 2 minutes.</Note>}
      {!d.strategies && !d.error && <Note>Loading Delta candles and working out the journal… this takes about two minutes after a restart.</Note>}
      <div className="flex flex-wrap items-center gap-2">
        <button className={btn(period === "live")} onClick={() => setPeriod("live")}>Live paper from {d.live_start_ist?.slice(5) || "start"}</button>
        <button className={btn(period === "all")} onClick={() => setPeriod("all")}>Include replay since {d.replay_from_ist?.slice(0, 10) || "1 Oct"}</button>
        {only && <button className={btn(false)} onClick={() => setOnly(null)}>Show all 5 strategies</button>}
      </div>
      {mk && (
        <div className="rounded-lg border border-[#1e2a4a] bg-[#0f172a]/60 px-4 py-3" data-testid="runs-market">
          <div className="text-[12px] text-slate-400">Market now (BTC over the last 30 days, at the {mk.as_of_ist} 4h close)</div>
          <div className="text-lg font-bold text-slate-50">{mk.type} market · {(mk.chg30 * 100).toFixed(1)}%</div>
          <div className="text-[12.5px] text-slate-300">
            {mk.type === "Up" ? "All 5 strategies are active." : `3 strategies are active (the candle runs without a market filter). ${upOnly.map((s) => s.wb).join(" and ")} trade only in an Up market (BTC up more than 5% over 30 days), so they are standing aside.`}
          </div>
        </div>
      )}
      <div className="text-[12px] text-slate-400">{only ? `Showing ${strats.find((s) => s.key === only)?.name}` : "All 5 strategies together"} · {period === "live" ? "live paper trades only" : "replay + live"}</div>
      <Totals s={tot} />
      <Card title="By strategy" sub="Click a row to see only that strategy's trades. Re-test = the independent check on Jan 2023 – Nov 2026 data." testid="runs-strategies">
        <div className="overflow-x-auto scroll-thin">
          <table className="desk-table">
            <thead><tr><th>#</th><th>Strategy</th><th>Re-test</th><th className="num">Trades</th><th className="num">Closed</th><th className="num">Open</th><th className="num">Won</th><th className="num">Lost</th><th className="num">Win ratio</th><th className="num">Gross P&L</th><th className="num">Net P&L</th><th className="num">Cumulative</th></tr></thead>
            <tbody>{strats.map((s) => { const x = s[period]; return (
              <tr key={s.key} className={`cursor-pointer ${only === s.key ? "bg-amber-500/10" : ""}`} onClick={() => setOnly(only === s.key ? null : s.key)}>
                <td className="font-mono">{s.rank}</td>
                <td>{s.name}<div className="text-[11px] text-slate-500">{s.wb} · {s.tf} · stop 2 x ATR · 1:{s.rr} · exit after {s.hold_h === 168 ? "7 days" : `${s.hold_h} h`}{s.market ? ` · ${s.market} market only` : ""}</div></td>
                <td><span className={`rounded border px-1.5 py-0.5 text-[11px] font-semibold ${VERDICT_TONE[s.retest.verdict]}`}>{s.retest.verdict}</span></td>
                <td className="num">{x.trades}</td><td className="num">{x.closed}</td><td className="num">{x.open}</td><td className="num">{x.wins}</td><td className="num">{x.losses}</td>
                <td className="num">{pctTxt(x.win_rate)}</td><td className={`num ${pnlTone(x.gross)}`}>{signedUsd(x.gross)}</td><td className={`num font-semibold ${pnlTone(x.net)}`}>{signedUsd(x.net)}</td>
                <td className={`num ${pnlTone(x.cumulative)}`}>{signedUsd(x.cumulative)}</td>
              </tr>); })}
            </tbody>
          </table>
        </div>
      </Card>
      {curve.length > 1 && (
        <Card title="Cumulative net P&L" sub="Closed trades, after fees">
          <LineChart series={[{ name: "Net P&L $", color: VIZ.s1, points: curve }]} xFmt={(v) => v} yFmt={(v) => n(v, 0)} unit=" $" height={150} />
        </Card>
      )}
      <Card title="Journal" sub="Newest first. Entry time is when the paper trade filled (IST). Click a row for the reason, size, fees and exit." testid="runs-table">
        <div className="overflow-x-auto scroll-thin">
          <table className="desk-table">
            <thead><tr><th>Date</th><th>Time</th><th>Strategy</th><th>Side</th><th className="num">Entry</th><th className="num">SL</th><th className="num">TP</th><th>Result</th><th className="num">Exit</th><th className="num">Gross</th><th className="num">Net P&L</th><th className="num">Running</th></tr></thead>
            <tbody>
              {rows.map((r) => { const id = `${r.strategy}-${r.entry_t}`; return (
                <React.Fragment key={id}>
                  <tr className="cursor-pointer" onClick={() => setOpen(open === id ? null : id)}>
                    <td className="font-mono">{r.entry_ist.slice(0, 10)}</td><td className="font-mono">{r.entry_ist.slice(11, 16)}</td>
                    <td>{r.wb}<div className="text-[11px] text-slate-500">{r.period === "Replay" ? "replay" : "live paper"} · {r.market} market</div></td>
                    <td className={r.side === "LONG" ? "text-teal-300" : "text-red-300"}>{r.side}</td>
                    <td className="num">{n(r.entry, 1)}</td><td className="num">{n(r.sl, 1)}</td><td className="num">{n(r.tp, 1)}</td>
                    <td className={RESULT_TONE[r.result] || "text-slate-400"}>{r.result}</td>
                    <td className="num">{r.exit_price != null ? n(r.exit_price, 1) : r.mark != null ? n(r.mark, 1) : "—"}</td>
                    <td className={`num ${pnlTone(r.gross)}`}>{signedUsd(r.gross)}</td>
                    <td className={`num font-semibold ${pnlTone(r.net)}`}>{signedUsd(r.net)}</td>
                    <td className={`num ${pnlTone(only ? r.strategy_running : r.running_total)}`}>{signedUsd(only ? r.strategy_running : r.running_total)}</td>
                  </tr>
                  {open === id && <tr><td colSpan={12} className="bg-[#0f172a] text-xs"><Detail r={r} /></td></tr>}
                </React.Fragment>); })}
            </tbody>
          </table>
          {!rows.length && <div className="p-4 text-center text-xs text-slate-500">{period === "live" ? "No live paper trades yet. Signals are checked at every 4h close (01:30, 05:30, 09:30, 13:30, 17:30, 21:30 IST) and every 1h close; expect about 5–6 trades a month per run strategy. Switch to “Include replay” to see recent history." : "No trades in this period."}</div>}
        </div>
      </Card>
      <Card title="Rules" sub="Fixed. Paper only: this page never sends an order and sends no alerts.">
        <div className="space-y-3 text-xs text-slate-300">
          {strats.map((s) => (
            <div key={s.key} className="border-b border-[#1e2a4a]/60 pb-2">
              <div className="font-semibold text-slate-100">{s.rank}. {s.name} <span className="font-normal text-slate-400">({s.wb})</span> <span className={`ml-1 rounded border px-1.5 py-0.5 text-[10.5px] font-semibold ${VERDICT_TONE[s.retest.verdict]}`}>{s.retest.verdict}</span></div>
              <p><b>Rule:</b> {s.rule}</p>
              <p><b>How it works:</b> {s.how}</p>
              <p className="text-slate-400">Re-test Jan 2023 – Nov 2026: {s.retest.n} trades, {pctTxt(s.retest.win)} wins, {signedUsd(s.retest.net)} net after fees ({signedUsd(s.retest.per_trade)} a trade). Unseen Jan–Nov 2023: {signedUsd(s.retest.unseen_2023)} a trade.</p>
            </div>
          ))}
          <p><b>For every strategy:</b> entry at the next 5-minute open after the signal candle closes (market order). Stop and target are checked on Delta 5-minute candles; if one candle touches both, the stop counts. One trade at a time per strategy. $10,000 position. Fees: 0.05% taker on entry and exit + 18% GST (about $11.80 a round trip). Win = a trade that made money after fees.</p>
          <p className="text-slate-400">Replay rows are the same rules run on candles before the live start; they are not live paper trades.</p>
        </div>
      </Card>
    </div>
  );
}
