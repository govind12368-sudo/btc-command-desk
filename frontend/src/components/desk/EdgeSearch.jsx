import React from "react";
import { Card, Note, Expand } from "@/components/desk/ui";

const r = (v) => (v == null ? "—" : `${v > 0 ? "+" : v < 0 ? "−" : ""}${Math.abs(v).toFixed(2)}R`);
const tone = (v) => (v == null ? "text-slate-500" : v > 0 ? "text-teal-300" : "text-red-300");
const exit = (b) => `stop ${b.stop}× range, target ${b.rr >= 99 ? "none" : `${b.rr}× stop`}, ${b.hold_h}h hold`;
const cell = (s) => (s ? <><span className="font-mono">{s.win}%</span> wins, <span className={`font-mono font-semibold ${tone(s.r)}`}>{r(s.r)}</span> <span className="text-slate-500">({s.n} trades)</span></> : "—");

/** Search for a setup with a 50%+ win rate that makes money after fees. Static results of the 16 Nov 2026 study. */
export default function EdgeSearch({ e }) {
  if (!e) return null;
  const sw = e.switch_history;
  return (
    <Card title="Edge search: is there a 50% win setup that makes money?" testid="edge-search"
      sub={`${e.versions} versions of ${e.groups.reduce((a, g) => a + g.setups, 0)} setups tested on ${e.data}. Study of ${e.date}.`}>
      <Note tone="amber"><b>Result: {e.passed_train} of {e.versions} versions passed.</b> No setup had a win rate of 50% or more and a profit after fees in the training period, so the unseen test period was never needed. There is no confident setup to trade from this search.</Note>
      <div className="mt-3 overflow-x-auto scroll-thin">
        <table className="desk-table">
          <thead><tr><th>Group</th><th className="num">Setups</th><th className="num">Versions</th><th className="num">Passed training</th></tr></thead>
          <tbody>{e.groups.map((g) => <tr key={g.key}><td>{g.name}</td><td className="num font-mono">{g.setups}</td><td className="num font-mono">{g.versions}</td><td className="num font-mono font-semibold">{g.passed_train}</td></tr>)}</tbody>
        </table>
      </div>
      <div className="mt-3 grid grid-cols-1 gap-4 lg:grid-cols-2">
        <section>
          <h3 className="mb-1.5 text-[13px] font-semibold text-slate-100">Why nothing passed</h3>
          <ul className="list-disc space-y-1 pl-5 text-[12.5px] text-slate-300">
            <li>No edge even before fees: with fees set to zero, the best version of each intraday setup was between {r(e.gross.train_low)} and {r(e.gross.train_high)} in training and between {r(e.gross.val_low)} and {r(e.gross.val_high)} in validation.</li>
            <li>Random entries with the same exits win {e.random.win_low}–{e.random.win_high}% at 1:1 and score about zero before fees, then lose {r(e.random.net_high)} to {r(e.random.net_low)} a trade after fees.</li>
            <li>A high win rate is easy to get with a small target, and it still loses. The table below shows the best 50%+ version of each setup.</li>
          </ul>
        </section>
        <section>
          <h3 className="mb-1.5 text-[13px] font-semibold text-slate-100">Day-type switch in history</h3>
          <ul className="list-disc space-y-1 pl-5 text-[12.5px] text-slate-300">
            <li>{sw.trades} trades from {sw.period[0]} to {sw.period[1]}: {sw.win}% wins, <b className="text-red-300">{r(sw.mean_r)}</b> a trade after fees.</li>
            <li>A good run over a handful of live trades is within what chance produces. Keep it in paper mode until the live record is long.</li>
          </ul>
          <h3 className="mb-1.5 mt-3 text-[13px] font-semibold text-slate-100">The two closest, neither proven</h3>
          <ul className="list-disc space-y-1 pl-5 text-[12.5px] text-slate-300">{e.closest.map((c) => <li key={c.name}><b>{c.name}.</b> {c.text}</li>)}</ul>
        </section>
      </div>
      <Expand label="Every setup: best version, and best version with 50% or more wins" testid="edge-search-table">
        <div className="overflow-x-auto scroll-thin"><table className="desk-table">
          <thead><tr><th>Setup</th><th>Version</th><th>Training</th><th>Validation</th></tr></thead>
          <tbody>{e.families.flatMap((f) => [
            <tr key={f.name}><td className="font-semibold">{f.name}</td><td className="text-[11.5px] text-slate-400">Best result: {exit(f.best)}</td><td>{cell(f.best.train)}</td><td>{cell(f.best.val)}</td></tr>,
            <tr key={f.name + "50"}><td /><td className="text-[11.5px] text-slate-400">{f.best_50 ? `Best with 50%+ wins: ${exit(f.best_50)}` : "No version reached 50% wins"}</td><td>{f.best_50 ? cell(f.best_50.train) : "—"}</td><td>{f.best_50 ? cell(f.best_50.val) : "—"}</td></tr>,
          ])}</tbody>
        </table></div>
        <p className="mt-2">R is the average result per trade as a share of the amount risked, after fees. Positive in training but not in validation means it did not hold up.</p>
      </Expand>
      <Note tone="plain">{e.rules} Pass mark: {e.pass_mark} Periods: training {e.splits.train}; validation {e.splits.validation}; test {e.splits.test}. Past results do not promise future results.</Note>
    </Card>
  );
}
