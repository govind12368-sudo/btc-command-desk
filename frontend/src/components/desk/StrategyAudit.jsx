import React, { useState } from "react";
import { useFetch } from "@/hooks/useFetch";
import { Card } from "@/components/desk/ui";
import { n, usd, ist } from "@/lib/fmt";
import { BOOK_NAME, DayChip, EvidenceCard, PeriodSwitch } from "@/components/desk/Books";

const pnl = (v) => ((v || 0) >= 0 ? "text-teal-300" : "text-red-300");
const pct = (a, b) => (b ? `${Math.round((100 * a) / b)}%` : "—");

/** Senior-desk review: which way of trading the scalp signal worked, when, why, and what to do about it. */
export default function StrategyAudit() {
  const { data: a } = useFetch("/strategy-audit", 120000);
  const [period, setPeriod] = useState("all");
  if (!a) return <Card title="Strategy audit"><p className="text-sm text-slate-500">Loading the strategy audit…</p></Card>;
  const books = a.books?.[period] || {};
  const w = a.why || {};
  const ev = a.evidence;
  const best = ["follow", "plan_a", "switch"].filter((k) => books[k]).sort((x, y) => books[y].net - books[x].net)[0];
  return (
    <div className="space-y-4" data-testid="strategy-audit">
      <Card title="Strategy audit: follow the signal, Plan A, or switch by day type" testid="audit-books"
        sub={`Same signals, same 10-minute entry, one trade at a time, 6-hour exit, same fees. Updated ${ist(a.computed_at)}.`}
        right={<PeriodSwitch value={period} onChange={setPeriod} sinceDate={a.books?.since_date} />}>
        <div className="overflow-x-auto scroll-thin">
          <table className="desk-table">
            <thead><tr><th>Method</th><th>Signals</th><th>Taken</th><th>Skipped</th><th>Target / Stop / 6h</th><th>Win rate</th><th>Net P&L</th><th>Worst drawdown</th></tr></thead>
            <tbody>
              {["follow", "plan_a", "switch"].map((k) => books[k] && (
                <tr key={k}>
                  <td className={k === best ? "font-semibold text-slate-50" : ""}>{BOOK_NAME[k]}</td><td className="font-mono">{books[k].signals}</td><td className="font-mono">{books[k].taken}</td>
                  <td className="font-mono">{books[k].skipped + (books[k].stood_aside || 0)}</td><td className="font-mono">{books[k].target} / {books[k].stop} / {books[k].time}</td>
                  <td className="font-mono">{books[k].win_rate == null ? "—" : `${Math.round(books[k].win_rate * 100)}%`}</td>
                  <td className={`font-mono ${pnl(books[k].net)}`}>{usd(books[k].net)}</td><td className="font-mono text-red-300">{usd(books[k].max_drawdown)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <p className="mt-2 text-[11.5px] text-slate-500">Switch = range → Plan A, trend with the signal → follow, anything else → no trade. The day type is frozen at the minute each signal was captured.</p>
      </Card>

      <Card title="Conclusion and recommendation" testid="audit-conclusion">
        <ul className="list-disc space-y-1.5 pl-5 text-[13px] text-slate-200">
          {best && <li>In this period the best of the three is <b>{BOOK_NAME[best]}</b> at {usd(books[best].net)}. A short period swings a lot: one method leads for some days, then the other.</li>}
          {ev && <li>The long test is the stronger evidence: over {ev.signals.toLocaleString("en-US")} signals, Follow averaged {n(ev.follow.mean_r, 2)}R a trade, Plan A {n(ev.plan_a.mean_r, 2)}R and the day-type switch {n(ev.switch.mean_r, 2)}R. All three lose, in almost every month.</li>}
          {ev && <li><b>Root cause:</b> after a scalp signal, price is about as likely to go either way. Fees then take about {n(ev.fee_r, 2)}R from every trade, whichever side is traded.</li>}
          {ev && <li><b>Day type:</b> a trend call was followed by more trend only {ev.after_trend.continued_pct}% of the time, so it cannot pick the winning side in advance.</li>}
          <li><b>Recommendation:</b> keep Auto-Trade in Paper. Do not go Live with Follow, Plan A or the switch on this evidence.</li>
          <li><b>What could change the result:</b> a signal with a measured edge, or a much lower cost per trade (limit-order entries, fewer trades, wider targets). Any new idea should pass the same 11-month test first.</li>
        </ul>
      </Card>

      <Card title="Why targets and stops were hit" sub={`${w.n || 0} filled signals so far, on 1-minute Delta candles.`} testid="audit-why">
        <ul className="list-disc space-y-1.5 pl-5 text-[13px] text-slate-200">
          <li>The scalp signal is a momentum signal: it sells after a strong down candle in a downtrend and buys the opposite. Its target needs price to keep going 1.5 stop-distances.</li>
          {w.ran_1_5 && <li>Price ran at least 1.5 stop-distances the signal's way in {w.ran_1_5.n} signals: Follow won {w.ran_1_5.follow_wins} of them.</li>}
          {w.ran_under_1 && <li>Price never ran even 1 stop-distance the signal's way in {w.ran_under_1.n} signals: Plan A won {w.ran_under_1.plan_a_wins} of them.</li>}
          <li>Both ways lost on {w.both_lost} signals (price hit one stop, then the other).</li>
          {w.avg_fee != null && <li>Fees average {usd(w.avg_fee)} a trade against {usd(w.avg_risk)} of risk. Follow needs {pct(w.breakeven_follow, 1)} wins to break even, Plan A needs {pct(w.breakeven_plan_a, 1)}.</li>}
          {w.long && <li>LONG signals: Follow won {w.long.follow_wins} of {w.long.n}. SHORT signals: Follow won {w.short.follow_wins} of {w.short.n}.</li>}
        </ul>
      </Card>

      <Card title="Day by day" sub="Day type is judged after the day has finished (IST). P&L counts every filled signal captured that day." testid="audit-days">
        <div className="overflow-x-auto scroll-thin">
          <table className="desk-table">
            <thead><tr><th>Date</th><th>Finished day</th><th>Signals</th><th>Follow</th><th>Plan A</th><th>Switch</th><th>Day range</th><th>Day change</th></tr></thead>
            <tbody>
              {[...(a.days || [])].reverse().map((d) => (
                <tr key={d.date}>
                  <td className="font-mono">{d.date}</td><td>{d.eod === "TODAY" ? <span className="text-slate-400">in progress</span> : <DayChip type={d.eod} />}</td><td className="font-mono">{d.n}</td>
                  <td className={`font-mono ${pnl(d.follow)}`}>{usd(d.follow)}</td><td className={`font-mono ${pnl(d.plan_a)}`}>{usd(d.plan_a)}</td>
                  <td className={`font-mono ${pnl(d.switch)}`}>{d.switch_n ? `${usd(d.switch)} (${d.switch_n})` : "no trade"}</td>
                  <td className="font-mono">{d.range_pct == null ? "—" : `${n(d.range_pct, 1)}%`}</td><td className="font-mono">{d.change_pct == null ? "—" : `${d.change_pct > 0 ? "+" : ""}${n(d.change_pct, 1)}%`}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>
      <EvidenceCard ev={ev} />
    </div>
  );
}
