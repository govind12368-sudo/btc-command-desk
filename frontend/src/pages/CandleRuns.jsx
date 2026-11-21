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
      <Stat label="Total trades (positions)" value={s.trades} sub={`${s.shared ?? 0} shared by 2+ strategies`} />
      <Stat label="Closed" value={s.closed} sub={`${s.wins} won · ${s.losses} lost · parts: ${s.targets} target, ${s.stops} stop, ${s.time_exits} time`} />
      <Stat label="Open" value={s.open} tone="text-sky-300" sub={s.open ? `open P&L ${signedUsd(s.open_pnl)}` : null} />
      <Stat label="Win ratio" value={pctTxt(s.win_rate)} sub={s.closed ? `${s.wins} of ${s.closed} closed` : "no closed trades yet"} />
      <Stat label="Gross P&L (before fees)" value={signedUsd(s.gross)} tone={pnlTone(s.gross)} sub="closed trades" />
      <Stat label="Net P&L (after fees)" value={signedUsd(s.net)} tone={pnlTone(s.net)} sub={`fees ${signedUsd(-s.fees)}`} />
      <Stat label="Cumulative profit" value={signedUsd(s.cumulative)} tone={pnlTone(s.cumulative)} sub="net closed + open at live price" />
    </div>
  );
}

function Detail({ p }) {
  const kv = [["Signal (candle close)", p.signal_ist], ["Entry filled", `${p.entry_ist} at ${n(p.entry, 1)}`], ["Market (30 days)", p.chg30 == null ? "—" : `${p.market} · ${(p.chg30 * 100).toFixed(1)}%`],
    ["Stop (shared, 2 x ATR)", `${n(p.sl, 1)} ($${n(p.d, 1)} away)`], ["Time exit at", p.time_exit_ist], ["Data", p.period === "Backtest" ? "Binance 5m (3-year backtest)" : "Delta 5m"]];
  return (
    <div className="space-y-2">
      <div className="grid gap-x-6 gap-y-1 sm:grid-cols-2 lg:grid-cols-3">
        {kv.map(([k, v]) => <div key={k} className="flex justify-between gap-3 border-b border-[#1e2a4a]/60 py-1"><span className="text-slate-400">{k}</span><span className="font-mono text-slate-200">{v}</span></div>)}
      </div>
      <table className="desk-table">
        <thead><tr><th>Part</th><th className="num">Share</th><th className="num">Size</th><th className="num">Target</th><th>Result</th><th>Exit</th><th className="num">Gross</th><th className="num">Fees</th><th className="num">Net</th><th className="num">If traded alone ($10,000)</th></tr></thead>
        <tbody>{p.parts.map((x) => (
          <tr key={x.wb}><td>{x.wb} · 1:{x.rr}</td><td className="num">{Math.round(x.share * 100)}%</td><td className="num">{x.size} BTC</td><td className="num">{n(x.tp, 1)}</td>
            <td className={RESULT_TONE[x.result] || ""}>{x.result}</td><td className="font-mono">{x.exit_ist ? `${x.exit_ist} @ ${n(x.exit_price, 1)}` : x.mark != null ? `open, ${n(x.mark, 1)}` : "—"}</td>
            <td className={`num ${pnlTone(x.gross)}`}>{signedUsd(x.gross)}</td><td className="num">{signedUsd(-x.fees)}</td><td className={`num font-semibold ${pnlTone(x.net)}`}>{signedUsd(x.net)}</td>
            <td className={`num ${pnlTone(x.full_net)}`}>{signedUsd(x.full_net)}</td></tr>))}
        </tbody>
      </table>
      <div className="text-slate-300">Why: {p.reason}</div>
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
  const inPeriod = (r) => period === "full" || (period === "all" ? r.period !== "Backtest" : r.period === "Live");
  const posP = (d.positions || []).filter(inPeriod);
  const view = (p) => { if (!only) return p; const x = p.parts.find((q) => q.strategy === only); return x ? { ...p, gross: x.gross, fees: x.fees, net: x.net, result: x.result, parts: [x], status: x.status } : null; };
  const rows0 = posP.map(view).filter(Boolean);
  let run = 0;
  const running = new Map([...rows0].sort((a, b) => a.entry_t - b.entry_t).map((r) => [r, (run += r.net)]));
  const rows = rows0;
  const tot = only ? strats.find((s) => s.key === only)?.[period] : d.totals?.[period];
  let eq = 0;
  const curve = [...rows].reverse().filter((r) => r.status === "CLOSED").map((r) => ({ t: r.exit_ist || r.parts[0].exit_ist, v: (eq += r.net) }));
  const btn = (on) => `rounded px-3 py-1.5 text-xs font-semibold ${on ? "bg-amber-500 text-slate-950" : "border border-[#1e2a4a] text-slate-300 hover:text-white"}`;
  const mk = d.market_now;
  const upOnly = strats.filter((s) => s.market === "Up");
  return (
    <div className="space-y-4" data-testid="runs-page">
      <PageHead title={`Candle Runs – ${strats.length || 3} paper strategies`} sub={`Paper trades only: no real orders and no alerts. From your backtest workbook, re-tested. Updated ${ist(d.computed_at)} · BTC ${n(d.live_price, 1)}${d.last_candle_ist ? ` · last 5m candle ${d.last_candle_ist}` : ""}`} />
      {d.error && <Note tone="red">Last update failed: {d.error}. It retries every 2 minutes.</Note>}
      {!d.strategies && !d.error && <Note>Loading Delta candles and working out the journal… this takes about two minutes after a restart.</Note>}
      <div className="flex flex-wrap items-center gap-2">
        <button className={btn(period === "live")} onClick={() => setPeriod("live")}>Live paper from {d.live_start_ist?.slice(5) || "start"}</button>
        <button className={btn(period === "all")} onClick={() => setPeriod("all")}>Include replay since {d.replay_from_ist?.slice(0, 10) || "1 Oct"}</button>
        <button className={btn(period === "full")} onClick={() => setPeriod("full")}>3-year journal since {d.backtest?.from_ist || "2023-01-02"}</button>
        {only && <button className={btn(false)} onClick={() => setOnly(null)}>Show all strategies</button>}
      </div>
      {mk && (
        <div className="rounded-lg border border-[#1e2a4a] bg-[#0f172a]/60 px-4 py-3" data-testid="runs-market">
          <div className="text-[12px] text-slate-400">Market now (BTC over the last 30 days, at the {mk.as_of_ist} 4h close)</div>
          <div className="text-lg font-bold text-slate-50">{mk.type} market · {(mk.chg30 * 100).toFixed(1)}%</div>
          <div className="text-[12.5px] text-slate-300">
            {upOnly.length && mk.type !== "Up" ? `${upOnly.map((s) => s.wb).join(" and ")} trade only in an Up market, so they are standing aside.` : `All ${strats.length} strategies trade in every market type (up, range and down).`}
          </div>
        </div>
      )}
      <div className="text-[12px] text-slate-400">{only ? `Showing ${strats.find((s) => s.key === only)?.name}` : `All ${strats.length} strategies together`} · {period === "live" ? "live paper trades only" : period === "all" ? "replay + live" : `3-year backtest (${d.backtest?.trades ?? 0} trades to ${d.backtest?.to_ist?.slice(0, 10) || "30 Sep 2026"}) + replay + live`}</div>
      {period === "full" && <Note>3-year rows are a backtest, not live trades: the site's own Candle Runs code run on {d.backtest?.source?.split(",")[0] || "Binance BTCUSDT 5-minute candles"} from {d.backtest?.from_ist || "2 Jan 2023"} to signals before {d.backtest?.to_ist || "1 Oct 2026"}. From then on the page uses Delta candles (replay), and from {d.live_start_ist} the live paper trades.</Note>}
      <Totals s={tot} />
      <Card title="By strategy" sub="Each strategy's part of the positions (its share when it signals together with another). The rows add up to the totals above. Click a row to see only that strategy." testid="runs-strategies">
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
      <Card title="Journal" sub={d.position_rule || "One $10,000 position per entry time; strategies that signal together share it."} testid="runs-table">
        <div className="mb-2 text-[11.5px] text-slate-400">Newest first. Entry time is when the paper position filled (IST). TP shows each part's own target. Click a row for each part's size, exit, fees and net.</div>
        <div className="overflow-x-auto scroll-thin">
          <table className="desk-table">
            <thead><tr><th>Date</th><th>Time</th><th>Strategy</th><th>Side</th><th className="num">Entry</th><th className="num">SL</th><th className="num">TP</th><th>Result</th><th className="num">Exit</th><th className="num">Gross</th><th className="num">Net P&L</th><th className="num">Running</th></tr></thead>
            <tbody>
              {rows.map((r) => (
                <React.Fragment key={r.id}>
                  <tr className={`cursor-pointer ${r.members.length > 1 && !only ? "bg-indigo-500/5" : ""}`} onClick={() => setOpen(open === r.id ? null : r.id)}>
                    <td className="font-mono">{r.entry_ist.slice(0, 10)}</td><td className="font-mono">{r.entry_ist.slice(11, 16)}</td>
                    <td>{only ? r.parts[0].wb : r.members.join(" + ")}
                      {r.members.length > 1 && <span className="ml-1.5 rounded border border-indigo-400/40 bg-indigo-500/15 px-1.5 py-0.5 text-[10.5px] font-semibold text-indigo-200">{only ? `shared ${Math.round(r.parts[0].share * 100)}%` : `1 position · ${r.members.length} signals`}</span>}
                      <div className="text-[11px] text-slate-500">{r.period === "Backtest" ? "3-yr backtest" : r.period === "Replay" ? "replay" : "live paper"} · {r.market} market</div></td>
                    <td className={r.side === "LONG" ? "text-teal-300" : "text-red-300"}>{r.side}</td>
                    <td className="num">{n(r.entry, 1)}</td><td className="num">{n(r.sl, 1)}</td>
                    <td className="num">{r.parts.map((x) => n(x.tp, 1)).join(" / ")}</td>
                    <td className={RESULT_TONE[r.result] || "text-slate-200"}>{r.result}</td>
                    <td className="num">{r.parts.map((x) => (x.exit_price != null ? n(x.exit_price, 1) : x.mark != null ? n(x.mark, 1) : "—")).join(" / ")}</td>
                    <td className={`num ${pnlTone(r.gross)}`}>{signedUsd(r.gross)}</td>
                    <td className={`num font-semibold ${pnlTone(r.net)}`}>{signedUsd(r.net)}</td>
                    <td className={`num ${pnlTone(running.get(r))}`}>{signedUsd(running.get(r))}</td>
                  </tr>
                  {open === r.id && <tr><td colSpan={12} className="bg-[#0f172a] text-xs"><Detail p={r} /></td></tr>}
                </React.Fragment>
              ))}
            </tbody>
          </table>
          {!rows.length && <div className="p-4 text-center text-xs text-slate-500">{period === "live" ? "No live paper trades yet. Signals are checked at every 4h close (01:30, 05:30, 09:30, 13:30, 17:30, 21:30 IST). Switch to “Include replay” or “3-year journal” to see history." : "No trades in this period."}</div>}
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
          <p><b>No double trades:</b> {d.position_rule} Example: when P&L #1 (1:3) and P&L #5 (1:2) signal together, one $10,000 SHORT is opened ($5,000 each), both halves use the same stop, one half closes at the 1:2 target and the other at the 1:3 target.</p>
          <p><b>For every strategy:</b> entry at the next 5-minute open after the signal candle closes (market order). Stop and target are checked on Delta 5-minute candles; if one candle touches both, the stop counts. One trade at a time per strategy. $10,000 position. Fees: 0.05% taker on entry and exit + 18% GST (about $11.80 a round trip). Win = a trade that made money after fees.</p>
          <p className="text-slate-400">Replay rows are the same rules run on candles before the live start; they are not live paper trades.</p>
        </div>
      </Card>
    </div>
  );
}
