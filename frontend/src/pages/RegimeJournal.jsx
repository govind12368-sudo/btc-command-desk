import React, { useState } from "react";
import { useFetch } from "@/hooks/useFetch";
import { Card, PageHead, Note } from "@/components/desk/ui";
import { n, signedUsd, ist } from "@/lib/fmt";
import { LineChart, VIZ } from "@/components/desk/charts";

const pnlTone = (v) => (v > 0 ? "text-teal-300" : v < 0 ? "text-red-300" : "text-slate-300");
const pctTxt = (v) => (v == null ? "—" : `${Math.round(v * 100)}%`);
const RESULT_TONE = { Target: "text-teal-300", Stop: "text-red-300", "Time exit": "text-amber-300", Open: "text-sky-300" };
const SIDE_TONE = { LONG: "text-teal-300", SHORT: "text-red-300" };

function Stat({ label, value, tone = "text-slate-50", sub }) {
  return (
    <div className="rounded-lg border border-[#1e2a4a] bg-[#0f172a]/60 px-3 py-2.5">
      <div className="text-[11.5px] text-slate-400">{label}</div>
      <div className={`font-mono text-lg font-bold ${tone}`}>{value}</div>
      {sub && <div className="text-[11px] text-slate-500">{sub}</div>}
    </div>
  );
}

function Totals({ s, testid }) {
  if (!s) return null;
  return (
    <div className="grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-6" data-testid={testid}>
      <Stat label="Total trades" value={s.trades} sub={`${s.targets} target · ${s.stops} stop · ${s.time_exits} 6h`} />
      <Stat label="Closed" value={s.closed} />
      <Stat label="Open" value={s.open} tone="text-sky-300" sub={s.open ? `open P&L ${signedUsd(s.open_pnl)}` : null} />
      <Stat label="Win rate" value={pctTxt(s.win_rate)} sub={s.closed ? `${s.wins} of ${s.closed} closed` : null} />
      <Stat label="Net P&L after fees" value={signedUsd(s.net)} tone={pnlTone(s.net)} sub="closed trades" />
      <Stat label="Cumulative P&L" value={signedUsd(s.cumulative)} tone={pnlTone(s.cumulative)} sub="closed + open at live price" />
    </div>
  );
}

function ByRule({ rows }) {
  if (!rows?.length) return null;
  return (
    <div className="overflow-x-auto scroll-thin">
      <table className="desk-table">
        <thead><tr><th>Setup</th><th className="num">Trades</th><th className="num">Closed</th><th className="num">Win rate</th><th className="num">Avg R</th><th className="num">Net P&L</th></tr></thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.rule}><td>{r.rule}</td><td className="num">{r.trades}</td><td className="num">{r.closed}</td><td className="num">{pctTxt(r.win_rate)}</td>
              <td className="num">{r.avg_r == null ? "—" : `${r.avg_r > 0 ? "+" : ""}${r.avg_r.toFixed(2)}R`}</td><td className={`num ${pnlTone(r.net)}`}>{signedUsd(r.net)}</td></tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function Detail({ r }) {
  const kv = [["Signal", `${r.signal_id} (${r.signal_side})`], ["Market type", `${r.market} (day type ${r.day_type})`],
    ["RSI 15m", n(r.rsi, 1)], ["Stop ÷ hourly ATR", r.stop_atr == null ? "—" : `${n(r.stop_atr, 2)}x (ATR ${n(r.atr1h, 1)})`],
    ["Volume vs average", r.vr == null ? "—" : `${n(r.vr, 2)}x`], ["Trends 15m / 1h / 4h", `${r.t15 || "—"} / ${r.t1h || "—"} / ${r.t4h || "—"}`],
    ["Price vs 4h ago", r.chg4h == null ? "—" : `${(r.chg4h * 100).toFixed(2)}%`], ["1R (stop distance)", `$${n(r.d, 1)}`],
    ["Size", r.size ? `${r.size} BTC ($1,000 × 10x)` : "—"], ["Filled", r.fill_ist || "—"], ["Exit", r.exit_ist ? `${r.exit_ist} at ${n(r.exit_price, 1)}` : r.mark ? `open, marked at ${n(r.mark, 1)}` : "—"],
    ["Gross / fees", r.gross == null ? "—" : `${signedUsd(r.gross)} / ${signedUsd(-r.fees)}`], ["Result in R", r.r == null ? "—" : `${r.r > 0 ? "+" : ""}${r.r.toFixed(2)}R`],
    ["Best / worst move", r.mfe == null ? "—" : `+${r.mfe}R / −${r.mae}R`], ["Running total", r.running_total == null ? "—" : signedUsd(r.running_total)]];
  return (
    <div className="grid gap-x-6 gap-y-1 sm:grid-cols-2 lg:grid-cols-3">
      {kv.map(([k, v]) => <div key={k} className="flex justify-between gap-3 border-b border-[#1e2a4a]/60 py-1"><span className="text-slate-400">{k}</span><span className="font-mono text-slate-200">{v}</span></div>)}
      <div className="sm:col-span-2 lg:col-span-3 pt-1 text-slate-300">{r.reason}</div>
    </div>
  );
}

function Journal({ rows, reverse, showAll }) {
  const [open, setOpen] = useState(null);
  const list = showAll ? rows : rows.filter((r) => r.status);
  return (
    <div className="overflow-x-auto scroll-thin">
      <table className="desk-table">
        <thead><tr><th>Date</th><th>Time</th><th>{reverse ? "Reverse of" : "What we follow"}</th><th>Side</th><th className="num">Entry</th><th className="num">SL</th><th className="num">TP</th><th>Result</th><th className="num">Exit</th><th className="num">P&L</th><th className="num">Running</th></tr></thead>
        <tbody>
          {list.map((r) => {
            const traded = !!r.status && r.status !== "WAITING";
            const key = r.signal_id + (reverse ? "-r" : "");
            return (
              <React.Fragment key={key}>
                <tr className={`cursor-pointer ${traded ? "" : "opacity-60"}`} onClick={() => setOpen(open === key ? null : key)}>
                  <td className="font-mono">{r.time_ist.slice(0, 10)}</td><td className="font-mono">{r.time_ist.slice(11, 16)}</td>
                  <td>{reverse ? r.base_rule : r.rule}<div className="text-[11px] text-slate-500">{r.market}{r.period === "Before live" ? " · before live" : ""}</div></td>
                  <td className={SIDE_TONE[r.side || r.trade_side] || "text-slate-500"}>{r.side || r.trade_side || "—"}</td>
                  <td className="num">{n(r.entry, 1)}</td><td className="num">{traded ? n(r.sl, 1) : "—"}</td><td className="num">{traded ? n(r.tp, 1) : "—"}</td>
                  <td className={RESULT_TONE[r.result] || "text-slate-400"}>{r.result || (r.rule === "Not filled" ? "Not filled" : "No trade")}</td>
                  <td className="num">{r.exit_price != null ? n(r.exit_price, 1) : r.mark != null ? n(r.mark, 1) : "—"}</td>
                  <td className={`num font-semibold ${pnlTone(r.net)}`}>{r.net == null ? "—" : signedUsd(r.net)}</td>
                  <td className={`num ${pnlTone(r.running_total)}`}>{r.running_total == null ? "" : signedUsd(r.running_total)}</td>
                </tr>
                {open === key && <tr><td colSpan={11} className="bg-[#0f172a] text-xs"><Detail r={r} /></td></tr>}
              </React.Fragment>
            );
          })}
        </tbody>
      </table>
      {!list.length && <div className="p-4 text-center text-xs text-slate-500">No trades yet.</div>}
    </div>
  );
}

export default function RegimeJournal() {
  const { data } = useFetch("/regime-journal", 60000);
  const [tab, setTab] = useState("regime");
  const [period, setPeriod] = useState("all");
  const [showAll, setShowAll] = useState(false);
  const book = data?.[tab];
  const rowsAll = book?.rows || [];
  const rows = period === "live" ? rowsAll.filter((r) => r.period === "Live") : rowsAll;
  const s = book ? (period === "live" ? book.live : book.all) : null;
  let eq = 0;
  const curve = [...rows].reverse().filter((r) => r.status === "CLOSED").map((r) => ({ t: r.exit_ist, v: (eq += r.net) }));
  const btn = (on) => `rounded px-3 py-1.5 text-xs font-semibold ${on ? "bg-amber-500 text-slate-950" : "border border-[#1e2a4a] text-slate-300 hover:text-white"}`;
  return (
    <div className="space-y-4" data-testid="regime-page">
      <PageHead title="Regime Strategy – Journal" sub={`Paper trades on every live scalp signal (no real orders). Updated ${ist(data?.computed_at)} · BTC ${n(data?.live_price, 1)}`} />
      {data?.error && <Note tone="red">Last update failed: {data.error}. It retries every 2 minutes.</Note>}
      {!data?.regime && !data?.error && <Note>Working out the journal… this takes a minute after a restart.</Note>}
      <div className="flex flex-wrap items-center gap-2">
        <button className={btn(tab === "regime")} onClick={() => setTab("regime")} data-testid="tab-regime">Regime trades</button>
        <button className={btn(tab === "reverse")} onClick={() => setTab("reverse")} data-testid="tab-reverse">Reverse (Plan A style)</button>
        <span className="mx-2 h-5 w-px bg-[#1e2a4a]" />
        <button className={btn(period === "all")} onClick={() => setPeriod("all")}>All since 3 Nov</button>
        <button className={btn(period === "live")} onClick={() => setPeriod("live")}>Live from {data?.live_start_ist?.slice(5) || "start"}</button>
      </div>
      <Totals s={s} testid="regime-totals" />
      {tab === "reverse" && <Note>Reverse = every Regime trade taken the other way round: same signal, same fill, same 1R stop distance, target 1.5R, 6-hour exit.</Note>}
      {curve.length > 1 && (
        <Card title="Cumulative net P&L" sub="Closed trades, after fees" testid="regime-curve">
          <LineChart series={[{ name: "Net P&L $", color: VIZ.s1, points: curve }]} xFmt={(v) => v} yFmt={(v) => n(v, 0)} unit=" $" height={150} />
        </Card>
      )}
      <Card title={tab === "regime" ? "Journal" : "Reverse journal"} sub="Newest first. Click a row for the market type, RSI, ATR, volume, trends, fees and the reason."
        right={tab === "regime" && <label className="flex items-center gap-1.5 text-xs text-slate-300"><input type="checkbox" checked={showAll} onChange={(e) => setShowAll(e.target.checked)} /> Show skipped signals</label>}
        testid="regime-table">
        <Journal rows={rows} reverse={tab === "reverse"} showAll={tab === "regime" && showAll} />
      </Card>
      <div className="grid gap-4 lg:grid-cols-2">
        <Card title="By setup" sub={period === "live" ? "Live period" : "All since 3 Nov"}><ByRule rows={period === "live" ? book?.by_rule_live : book?.by_rule} /></Card>
        <Card title="By market type" sub="All since 3 Nov">
          <ByRule rows={(book?.by_market || []).map((m) => ({ ...m, rule: m.market }))} />
        </Card>
      </div>
      <Card title="Daily summary" sub="Regime trades per IST day; the last two columns are the reverse book">
        <div className="overflow-x-auto scroll-thin">
          <table className="desk-table">
            <thead><tr><th>Date</th><th className="num">Signals</th><th className="num">Trades</th><th className="num">Target</th><th className="num">Stop</th><th className="num">6h</th><th className="num">Open</th><th className="num">Win rate</th><th className="num">Net P&L</th><th className="num">Cumulative</th><th className="num">Reverse net</th><th className="num">Reverse cum.</th></tr></thead>
            <tbody>
              {(data?.daily || []).map((d) => (
                <tr key={d.date}><td className="font-mono">{d.date}</td><td className="num">{d.signals}</td><td className="num">{d.trades}</td><td className="num">{d.targets}</td><td className="num">{d.stops}</td><td className="num">{d.time_exits}</td><td className="num">{d.open}</td>
                  <td className="num">{pctTxt(d.win_rate)}</td><td className={`num ${pnlTone(d.net)}`}>{signedUsd(d.net)}</td><td className={`num ${pnlTone(d.cumulative)}`}>{signedUsd(d.cumulative)}</td>
                  <td className={`num ${pnlTone(d.rev_net)}`}>{signedUsd(d.rev_net)}</td><td className={`num ${pnlTone(d.rev_cumulative)}`}>{signedUsd(d.rev_cumulative)}</td></tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>
      <Card title="Rules" sub="Fixed. Paper only: this page never sends an order.">
        <div className="space-y-1.5 text-xs text-slate-300">
          <p><b>Market type</b> at the signal candle's close: Down trend = Trend down day, or Unclear with the 4h trend down. Range = Range day, or Unclear with the 4h trend mixed. Up trend = Trend up day, or Unclear with the 4h trend up.</p>
          <p><b>Down trend:</b> Breakdown short (RSI 15m &lt; 35 and stop ≥ 0.6× hourly ATR) → SHORT; else Dip-bounce long (RSI ≥ 35, price below 4h ago, day not Trend down) → LONG.</p>
          <p><b>Range:</b> Trend follow (stop ≥ 0.6× ATR, volume ≥ 1.2× average, a 15m/1h/4h trend agrees with the signal) → signal side; else Trend fade (same checks, trends agree with the other side) → opposite; else Dip-bounce long (RSI ≥ 35, price below 4h ago) → LONG.</p>
          <p><b>Up trend:</b> Breakout long (RSI &gt; 65 and stop ≥ 0.6× ATR) → LONG; else Pullback long (RSI ≤ 50, price below 4h ago) → LONG. No shorts.</p>
          <p><b>Orders:</b> entry at the signal entry (the site's own fill rule), SL 1R = the signal's stop distance, TP 1.5R, close after 6 hours. $1,000 margin × 10x = $10,000 (size = $10,000 ÷ entry, 3 decimals). Max 2 trades per IST day. Fees: 0.05% taker (entry, stop, 6h exit), 0.02% maker (target), +18% GST. A minute that touches both SL and TP counts as SL.</p>
        </div>
      </Card>
    </div>
  );
}
