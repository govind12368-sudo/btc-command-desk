import React, { useState } from "react";
import { useFetch } from "@/hooks/useFetch";
import { Card, PageHead, Note, usePaged } from "@/components/desk/ui";
import { n, signedUsd, ist } from "@/lib/fmt";
import { LineChart, VIZ } from "@/components/desk/charts";

const pnlTone = (v) => (v > 0 ? "text-teal-300" : v < 0 ? "text-red-300" : "text-slate-300");
const pctTxt = (v) => (v == null ? "—" : `${Math.round(v * 100)}%`);
const RESULT_TONE = { Target: "text-teal-300", Stop: "text-red-300", "Time exit": "text-amber-300", Open: "text-sky-300" };

function Stat({ label, value, tone = "text-slate-50", sub }) {
  return (
    <div className="rounded-lg border border-line bg-panel2/60 px-3 py-2.5">
      <div className="text-[11.5px] text-slate-400">{label}</div>
      <div className={`font-mono text-lg font-bold ${tone}`}>{value}</div>
      {sub && <div className="text-[11px] text-slate-500">{sub}</div>}
    </div>
  );
}

function Totals({ s }) {
  if (!s) return null;
  return (
    <div className="grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-6" data-testid="flush-totals">
      <Stat label="Total trades" value={s.trades} sub={`${s.targets} target · ${s.stops} stop · ${s.time_exits} 6h`} />
      <Stat label="Closed" value={s.closed} />
      <Stat label="Open" value={s.open} tone="text-sky-300" sub={s.open ? `open P&L ${signedUsd(s.open_pnl)}` : null} />
      <Stat label="Win rate" value={pctTxt(s.win_rate)} sub={s.closed ? `${s.wins} of ${s.closed} closed · goal 50%+` : "goal 50%+"} />
      <Stat label="Net P&L after fees" value={signedUsd(s.net)} tone={pnlTone(s.net)} sub="closed trades" />
      <Stat label="Cumulative P&L" value={signedUsd(s.cumulative)} tone={pnlTone(s.cumulative)} sub="closed + open at live price" />
    </div>
  );
}

function Detail({ r }) {
  const kv = [["Signal", `${r.signal_id} (${r.signal_side})`], ["Market (30 days)", r.chg30 == null ? "—" : `${r.market} · ${(r.chg30 * 100).toFixed(1)}%`], ["Day type", r.day_type], ["RSI 15m", n(r.rsi, 1)],
    ["Stop ÷ hourly ATR", r.stop_atr == null ? "—" : `${n(r.stop_atr, 2)}x`], ["Trends 15m / 1h / 4h", `${r.t15 || "—"} / ${r.t1h || "—"} / ${r.t4h || "—"}`],
    ["1R (stop distance)", `$${n(r.d, 1)}`], ["Size", r.size ? `${r.size} BTC ($10,000)` : "—"], ["Filled", r.fill_ist || "—"],
    ["Exit", r.exit_ist ? `${r.exit_ist} at ${n(r.exit_price, 1)}` : r.mark ? `open, marked at ${n(r.mark, 1)}` : "—"],
    ["Gross / fees", r.gross == null ? "—" : `${signedUsd(r.gross)} / ${signedUsd(-r.fees)}`], ["Result in R", r.r == null ? "—" : `${r.r > 0 ? "+" : ""}${r.r.toFixed(2)}R`],
    ["Best / worst move", r.mfe == null ? "—" : `+${r.mfe}R / −${r.mae}R`]];
  return (
    <div className="grid gap-x-6 gap-y-1 sm:grid-cols-2 lg:grid-cols-3">
      {kv.map(([k, v]) => <div key={k} className="flex justify-between gap-3 border-b border-line/60 py-1"><span className="text-slate-400">{k}</span><span className="font-mono text-slate-200">{v}</span></div>)}
      <div className="pt-1 text-slate-300 sm:col-span-2 lg:col-span-3">{r.reason}</div>
    </div>
  );
}

export default function FlushShort() {
  const { data } = useFetch("/flush-short", 60000);
  const [period, setPeriod] = useState("all");
  const [showAll, setShowAll] = useState(false);
  const [open, setOpen] = useState(null);
  const d = data || {};
  const rowsAll = d.rows || [];
  const rows0 = period === "live" ? rowsAll.filter((r) => r.period === "Live") : rowsAll;
  const rows = showAll ? rows0 : rows0.filter((r) => r.rule !== "No trade");
  const [pageRows, pager] = usePaged(rows, 100, `${period}-${showAll}`);
  const s = period === "live" ? d.live : d.all;
  let eq = 0;
  const curve = [...rows0].reverse().filter((r) => r.status === "CLOSED").map((r) => ({ t: r.exit_ist, v: (eq += r.net) }));
  const ev = d.evidence;
  const btn = (on) => `rounded px-3 py-1.5 text-xs font-semibold ${on ? "bg-amber-500 text-slate-950" : "border border-line text-slate-300 hover:text-white"}`;
  return (
    <div className="space-y-4" data-testid="flush-page">
      <PageHead title="Flush Short v3 – Journal" sub={`Paper trades only (no real orders). Takes a SHORT breakdown signal only in a down market, when all six rules agree. Updated ${ist(d.computed_at)} · BTC ${n(d.live_price, 1)}`} />
      {d.error && <Note tone="red">Last update failed: {d.error}. It retries every 2 minutes.</Note>}
      {!d.rows && !d.error && <Note>Working out the journal… this takes a couple of minutes after a restart.</Note>}
      <div className="flex flex-wrap items-center gap-2">
        <button className={btn(period === "all")} onClick={() => setPeriod("all")}>All since 3 Nov</button>
        <button className={btn(period === "live")} onClick={() => setPeriod("live")}>Live from {d.live_start_ist?.slice(5) || "start"}</button>
      </div>
      {d.market_now && (
        <div className={`rounded-lg border px-4 py-3 ${d.market_now.active ? "border-red-500/50 bg-red-950/20" : "border-slate-600 bg-panel2/60"}`} data-testid="flush-market">
          <div className="text-[12px] text-slate-400">Market now (BTC over the last 30 days)</div>
          <div className="text-lg font-bold text-slate-50">{d.market_now.type} market · {(d.market_now.chg30 * 100).toFixed(1)}%</div>
          <div className="text-[12.5px] text-slate-300">{d.market_now.active ? "Flush Short is ACTIVE: it will take qualifying SHORT breakdown signals." : "Flush Short is STANDING ASIDE: it only trades when BTC is down more than 5% over 30 days. In up and range markets no tested rule beat luck."}</div>
        </div>
      )}
      <Totals s={s} />
      {ev && (
        <Note>
          26-month replay ({ev.period}): {ev.all.n} trades, {pctTxt(ev.all.win)} wins, {signedUsd(ev.all.usd)} a trade after fees.
          First half {pctTxt(ev.first_half.win)} / {signedUsd(ev.first_half.usd)}, second half {pctTxt(ev.second_half.win)} / {signedUsd(ev.second_half.usd)}.
          {ev.by_market && ` Same rules by market: ${Object.entries(ev.by_market).map(([k, v]) => `${k} ${v.n} trades, ${pctTxt(v.win)}, ${signedUsd(v.usd)}`).join(" · ")}.`} {ev.note}
        </Note>
      )}
      {curve.length > 1 && (
        <Card title="Cumulative net P&L" sub="Closed trades, after fees">
          <LineChart series={[{ name: "Net P&L $", color: VIZ.s1, points: curve }]} xFmt={(v) => v} yFmt={(v) => n(v, 0)} unit=" $" height={150} />
        </Card>
      )}
      <Card title="Journal" sub="Newest first. Click a row for RSI, ATR, trends, fees and the reason."
        right={<label className="flex items-center gap-1.5 text-xs text-slate-300"><input type="checkbox" checked={showAll} onChange={(e) => setShowAll(e.target.checked)} /> Show skipped signals ({d.signals_checked ?? 0} checked)</label>}
        testid="flush-table">
        <div className="overflow-x-auto scroll-thin">
          <table className="desk-table">
            <thead><tr><th>Date</th><th>Time</th><th>Decision</th><th>Side</th><th className="num">Entry</th><th className="num">SL</th><th className="num">TP</th><th>Result</th><th className="num">Exit</th><th className="num">P&L</th><th className="num">Running</th></tr></thead>
            <tbody>
              {pageRows.map((r) => {
                const traded = r.status === "OPEN" || r.status === "CLOSED";
                return (
                  <React.Fragment key={r.signal_id}>
                    <tr className={`cursor-pointer ${traded ? "" : "opacity-60"}`} onClick={() => setOpen(open === r.signal_id ? null : r.signal_id)}>
                      <td className="font-mono">{r.time_ist.slice(0, 10)}</td><td className="font-mono">{r.time_ist.slice(11, 16)}</td>
                      <td>{r.rule}<div className="text-[11px] text-slate-500">{r.market ? `${r.market} market` : "market ?"}{r.chg30 != null ? ` (${(r.chg30 * 100).toFixed(1)}%)` : ""}{r.period === "Before live" ? " · before live" : ""}</div></td>
                      <td className={r.trade_side ? "text-red-300" : "text-slate-500"}>{r.trade_side || "—"}</td>
                      <td className="num">{n(r.entry, 1)}</td><td className="num">{r.sl ? n(r.sl, 1) : "—"}</td><td className="num">{r.tp ? n(r.tp, 2) : "—"}</td>
                      <td className={RESULT_TONE[r.result] || "text-slate-400"}>{r.result || (r.rule === "Not filled" ? "Not filled" : "Skipped")}</td>
                      <td className="num">{r.exit_price != null ? n(r.exit_price, 1) : r.mark != null ? n(r.mark, 1) : "—"}</td>
                      <td className={`num font-semibold ${pnlTone(r.net)}`}>{r.net == null ? "—" : signedUsd(r.net)}</td>
                      <td className={`num ${pnlTone(r.running_total)}`}>{r.running_total == null ? "" : signedUsd(r.running_total)}</td>
                    </tr>
                    {open === r.signal_id && <tr><td colSpan={11} className="bg-panel2 text-xs"><Detail r={r} /></td></tr>}
                  </React.Fragment>
                );
              })}
            </tbody>
          </table>
          {pager}
          {!rows.length && <div className="p-4 text-center text-xs text-slate-500">No Flush Short trades yet. It is rare by design: about 1–2 trades a month, and only while BTC is in a down market.</div>}
        </div>
      </Card>
      {d.daily?.length > 0 && (
        <Card title="Daily summary" sub="Days with a Flush Short trade">
          <div className="overflow-x-auto scroll-thin">
            <table className="desk-table">
              <thead><tr><th>Date</th><th className="num">Trades</th><th className="num">Target</th><th className="num">Stop</th><th className="num">6h</th><th className="num">Open</th><th className="num">Win rate</th><th className="num">Net P&L</th><th className="num">Cumulative</th></tr></thead>
              <tbody>{d.daily.map((x) => (
                <tr key={x.date}><td className="font-mono">{x.date}</td><td className="num">{x.trades}</td><td className="num">{x.targets}</td><td className="num">{x.stops}</td><td className="num">{x.time_exits}</td><td className="num">{x.open}</td>
                  <td className="num">{pctTxt(x.win_rate)}</td><td className={`num ${pnlTone(x.net)}`}>{signedUsd(x.net)}</td><td className={`num ${pnlTone(x.cumulative)}`}>{signedUsd(x.cumulative)}</td></tr>
              ))}</tbody>
            </table>
          </div>
        </Card>
      )}
      <Card title="Rules" sub="Fixed. Paper only: this page never sends an order.">
        <div className="space-y-1.5 text-xs text-slate-300">
          <p><b>Take a scalp signal only when all six hold</b> at the signal candle's close: 1) the signal is SHORT; 2) RSI 15m is below 30; 3) it is not between 00:00 and 06:00 IST; 4) the stop is at least 0.6× the hourly ATR; 5) the 15m, 1h and 4h trends are all down; 6) BTC is in a down market: more than 5% lower than 30 days ago.</p>
          <p><b>Up and range markets:</b> Flush Short stands aside. Over 26 months no rule built on the site's signals beat luck in those markets (10,656 rules tried; luck alone produces as many "winners" as the search found).</p>
          <p><b>Trade:</b> SHORT at the signal entry with a post-only limit order (maker fee), stop 1R = the signal's stop distance, take profit 1.25R, close after 6 hours. $10,000 position. Fees: 0.02% maker in and at target, 0.05% taker at stop or 6-hour exit, +18% GST. A minute touching both stop and target counts as the stop.</p>
          <p><b>Alerts:</b> an email when a trade is taken, when it closes, and when an entry never fills (follows the site's alert switches).</p>
        </div>
      </Card>
    </div>
  );
}
