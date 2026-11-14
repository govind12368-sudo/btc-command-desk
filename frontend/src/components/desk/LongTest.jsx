import React from "react";
import { Card } from "@/components/desk/ui";
import { signedUsd } from "@/lib/fmt";

const tone = (v) => (v >= 0 ? "text-teal-300" : "text-red-300");

/** 26-month study of the scalp signal (Delta 5m candles, 1:2, 6-hour exit, fees). Static results of the 14 Nov 2026 study. */
export default function LongTest({ lt }) {
  if (!lt) return null;
  const a = lt.after_stop;
  return (
    <Card title="26-month test: why trades win or lose" testid="long-test"
      sub={`${lt.signals.toLocaleString("en-US")} scalp signals rebuilt with the site's rules, ${lt.period[0]} to ${lt.period[1]} (${lt.per_day} a day). Dollar figures at your size: ₹40,000 margin × 10 = $${lt.position_usd.toLocaleString("en-US")} position, fees included.`}>
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <section>
          <h3 className="mb-1.5 text-[13px] font-semibold text-slate-100">The signal compared with chance</h3>
          <ul className="list-disc space-y-1 pl-5 text-[12.5px] text-slate-300">
            <li>Signals: {lt.signal.win}% wins, <b className={tone(lt.signal.net_trade)}>{signedUsd(lt.signal.net_trade)}</b> a trade.</li>
            <li>Random entries, random direction, same rules: {lt.random.win_low}–{lt.random.win_high}% wins, {signedUsd(lt.random.net_low)} to {signedUsd(lt.random.net_high)} a trade.</li>
            <li>So the signal does no better than chance. The loss is the fee on each trade.</li>
            <li>{lt.grid.tested} stop / target / hold versions and {lt.entries.tested} entry versions tested: <b>{lt.grid.profitable + lt.entries.profitable}</b> made money after fees.</li>
          </ul>
          <h3 className="mb-1.5 mt-3 text-[13px] font-semibold text-slate-100">What happens on a stop</h3>
          <ul className="list-disc space-y-1 pl-5 text-[12.5px] text-slate-300">
            <li>{a.stopped_pct}% of trades hit the stop, usually within about {a.median_min} minutes; {a.never_half_pct}% of those never went half-way in our favour.</li>
            <li>In the 6 hours after a stop, price came back to the entry {a.back_to_entry_pct}% of the time and reached the target level {a.reached_target_pct}% of the time.</li>
            <li>Waiting for a better price or re-entering after the stop was tested too: it still lost, because the times price does not come back cost as much as the times it does.</li>
          </ul>
        </section>
        <section>
          <h3 className="mb-1.5 text-[13px] font-semibold text-slate-100">Win rate is set by the target size, not by skill</h3>
          <div className="overflow-x-auto scroll-thin">
            <table className="desk-table">
              <thead><tr><th>Target</th><th className="num">Wins of 8</th><th className="num">Avg win</th><th className="num">Avg loss</th><th className="num">Net / trade</th></tr></thead>
              <tbody>{lt.targets.map(([r, w, aw, al, net]) => (
                <tr key={r}><td>{r} × stop{r === 2 ? " (now)" : ""}</td><td className="num font-mono">{(w * 8 / 100).toFixed(1)}</td><td className="num font-mono text-teal-300">{signedUsd(aw)}</td>
                  <td className="num font-mono text-red-300">{signedUsd(al)}</td><td className={`num font-mono ${tone(net)}`}>{signedUsd(net)}</td></tr>
              ))}</tbody>
            </table>
          </div>
        </section>
        <section>
          <h3 className="mb-1.5 text-[13px] font-semibold text-slate-100">Trade management (same entries)</h3>
          <table className="desk-table">
            <thead><tr><th>Rule</th><th className="num">Win rate</th><th className="num">Net (bps)</th></tr></thead>
            <tbody>{lt.management.map(([nm, w, b]) => <tr key={nm}><td>{nm}</td><td className="num font-mono">{w}%</td><td className={`num font-mono ${tone(b)}`}>{b}</td></tr>)}</tbody>
          </table>
          <p className="mt-1 text-[11px] text-slate-500">bps = 0.01% of the position; fees are about 6–12 bps a round trip.</p>
        </section>
        <section>
          <h3 className="mb-1.5 text-[13px] font-semibold text-slate-100">Units and filters</h3>
          <ul className="list-disc space-y-1 pl-5 text-[12.5px] text-slate-300">
            <li>No unit separated winners from losers: {lt.units}.</li>
            <li>Weakest: {lt.weak[0][0]} lost {signedUsd(lt.weak[0][1])} a trade against {signedUsd(lt.weak[1][1])} for the rest; the 4th+ signal of a day was also weaker.</li>
            {lt.filters.map(([nm, pd, pm]) => <li key={nm}>{nm}: {pd} trades a day, <b className={tone(pm)}>{signedUsd(pm)}</b> a month.</li>)}
            <li>Only slow trend systems made money over 3 years. Best: {lt.slow_trend.name}, {lt.slow_trend.trades} trades in {lt.slow_trend.years} years, +{lt.slow_trend.total_pct}% (buy and hold +{lt.slow_trend.buy_hold_pct}%), worst drop −{lt.slow_trend.worst_drop_pct}%. Not proven.</li>
          </ul>
        </section>
      </div>
    </Card>
  );
}
