import React, { useState } from "react";
import { Link } from "react-router-dom";
import { useFetch } from "@/hooks/useFetch";
import { Card, PageHead, Note } from "@/components/desk/ui";
import { ist, signedUsd } from "@/lib/fmt";
import { LineChart, VIZ } from "@/components/desk/charts";

const pc = (v, d = 0) => (v == null ? "—" : `${(v * 100).toFixed(d)}%`);
const rr = (v) => (v == null ? "—" : `${v > 0 ? "+" : ""}${v.toFixed(2)}R`);
const ink = (v) => (v == null ? "text-slate-400" : v > 0 ? "text-teal-300" : v < 0 ? "text-red-300" : "text-slate-300");
const VERDICT_TONE = (v = "") =>
  v.startsWith("Proven") || v.startsWith("Profitable after fees (95") ? "border-teal-500/50 text-teal-200 bg-teal-950/30"
    : v.startsWith("Above") || v.startsWith("Profitable") || v.startsWith("Better") ? "border-sky-500/50 text-sky-200 bg-sky-950/30"
    : v.startsWith("Loses") || v.startsWith("Worse") ? "border-red-500/50 text-red-200 bg-red-950/30" : v.startsWith("Inside") ? "border-slate-500 text-slate-200"
    : v.startsWith("Losing") ? "border-red-500/50 text-red-200 bg-red-950/30" : v.startsWith("Too few") ? "border-slate-600 text-slate-300"
      : v.startsWith("Helps pick direction (strong") ? "border-teal-500/50 text-teal-200 bg-teal-950/30" : v.startsWith("Helps") ? "border-amber-500/50 text-amber-200 bg-amber-950/20"
        : v.startsWith("Changes") ? "border-sky-500/50 text-sky-200 bg-sky-950/30"
        : "border-slate-600 text-slate-300";
const Badge = ({ v }) => <span className={`inline-block rounded border px-1.5 py-0.5 text-[11px] ${VERDICT_TONE(v)}`}>{v || "—"}</span>;

function Leaderboard({ board }) {
  return (
    <div className="overflow-x-auto scroll-thin">
      <table className="desk-table">
        <thead>
          <tr><th>#</th><th>Strategy / journal</th><th className="num">Trades</th><th className="num">Profit per trade</th><th className="num">Likely range</th><th>Verdict</th>
            <th className="num">Win rate</th><th className="num">Break-even</th><th className="num">Net after fees</th><th className="num">More trades to prove</th>
            <th className="num">26 mo trades</th><th className="num">26 mo after fees</th><th className="num">26 mo before fees</th></tr>
        </thead>
        <tbody>
          {board.map((b, i) => {
            const s = b.recent || {}; const h = b.history || {};
            return (
              <tr key={b.key}>
                <td className="font-mono text-slate-500">{i + 1}</td>
                <td><Link className="text-amber-300 hover:underline" to={b.to}>{b.name}</Link></td>
                <td className="num">{s.n ?? 0}</td><td className={`num font-semibold ${ink(s.avg_r)}`}>{rr(s.avg_r)}</td>
                <td className="num text-slate-400">{s.r_lo == null ? "—" : `${rr(s.r_lo)} to ${rr(s.r_hi)}`}</td>
                <td><Badge v={s.verdict} /></td>
                <td className="num">{pc(s.win)}</td><td className="num">{pc(s.breakeven)}</td><td className={`num ${ink(s.net)}`}>{s.n ? signedUsd(s.net) : "—"}</td>
                <td className="num">{s.more_trades_to_prove == null ? "—" : s.more_trades_to_prove}</td>
                <td className="num">{h.n?.toLocaleString() ?? "—"}</td>
                <td className={`num ${ink(h.avg_r)}`}>{rr(h.avg_r)}</td><td className={`num ${ink(h.avg_r_gross)}`}>{rr(h.avg_r_gross)}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

const BOOKN = { follow: "Follow every signal", fade: "Plan A (reverse)", switch: "Day-type switch", regime: "Regime" };

function WhatIf({ w }) {
  const [book, setBook] = useState("switch");
  const cells = (w.cells || []).filter((c) => c.book === book);
  const cols = [["taker", 6], ["maker", 6], ["taker", 12], ["maker", 12]];
  const get = (sp, fee, h) => cells.find((c) => c.stop_pct === sp && c.entry_fee === fee && c.hold_h === h);
  const btn = (on) => `rounded px-2.5 py-1 text-xs font-semibold ${on ? "bg-amber-500 text-slate-950" : "border border-line text-slate-300"}`;
  return (
    <div>
      <div className="mb-2 flex flex-wrap gap-1.5">{Object.entries(BOOKN).map(([k, v]) => <button key={k} className={btn(book === k)} onClick={() => setBook(k)}>{v}</button>)}</div>
      <div className="overflow-x-auto scroll-thin">
        <table className="desk-table">
          <thead><tr><th>Stop</th>{cols.map(([f, h]) => <th key={f + h} className="num">{f === "maker" ? "Limit entry" : "Market entry"} · {h}h</th>)}<th className="num">Fees per trade</th><th className="num">Before fees</th><th className="num">Targets</th><th className="num">Stopped, then price came back</th><th className="num">6h exits</th><th className="num">1st half / 2nd half</th></tr></thead>
          <tbody>
            {(w.stops || []).map((sp) => {
              const b = get(sp, "taker", 6) || {};
              return (
                <tr key={sp}>
                  <td className="font-mono">{(sp * 100).toFixed(sp < 0.01 ? 2 : 1)}%</td>
                  {cols.map(([f, h]) => { const c = get(sp, f, h); return <td key={f + h} className={`num ${ink(c?.avg_r)} ${c?.live ? "outline outline-1 outline-amber-400" : ""}`} title={c?.verdict}>{c ? <>{c.usd_per_trade == null ? "" : `${c.usd_per_trade > 0 ? "+" : "−"}$${Math.abs(c.usd_per_trade).toFixed(2)}`}<div className="text-[10.5px] text-slate-500">{rr(c.avg_r)}</div></> : "—"}</td>; })}
                  <td className="num text-red-300">{b.fee_r == null ? "—" : `−${b.fee_r.toFixed(2)}R`}</td>
                  <td className={`num ${ink(b.avg_r_gross)}`}>{rr(b.avg_r_gross)}</td><td className="num">{pc(b.win)}</td>
                  <td className="num">{pc(b.noise_stops)}</td><td className="num">{pc(b.time_exits)}</td>
                  <td className="num">{rr(b.first_half_r)} / {rr(b.second_half_r)}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <Note tone="plain">Dollars a trade after fees (same $10,000 position everywhere), with R below, {w.signals?.toLocaleString()} signals {w.period?.join(" – ")}. The outlined cell is today's live setting (0.5% stop, market entry, 6h). Detail columns are for market entry, 6h. Hover a cell for its verdict.</Note>
    </div>
  );
}

function Drift({ d }) {
  if (!d.points?.length) return null;
  const band = (v) => d.points.map((p) => ({ t: p.t, v }));
  const series = [{ name: "Last 30 trades, R", color: VIZ.brand, points: d.points }];
  if (d.band_lo != null) series.push({ name: "History low", color: VIZ.neutral, points: band(d.band_lo) }, { name: "History high", color: VIZ.neutral, points: band(d.band_hi) });
  return (
    <div className="rounded-lg border border-line bg-panel2/50 p-2.5">
      <div className="mb-1 flex flex-wrap items-center justify-between gap-2"><span className="text-sm font-semibold text-slate-100">{d.name}</span><Badge v={d.status} /></div>
      <div className="mb-1 text-[12px] text-slate-400">Last {d.window} trades: <b className={ink(d.rolling_r)}>{rr(d.rolling_r)}</b> a trade · history {rr(d.hist_r)} · usual range {rr(d.band_lo)} to {rr(d.band_hi)}</div>
      <LineChart series={series} xFmt={(v) => v} yFmt={(v) => `${v.toFixed(2)}R`} height={130} testid={`drift-${d.key}`} />
    </div>
  );
}

function Reasons({ title, rows }) {
  const max = Math.max(1, ...(rows || []).map((r) => r.n));
  return (
    <div>
      <div className="mb-1.5 text-sm font-semibold text-slate-200">{title}</div>
      {(rows || []).map((r) => (
        <div key={r.reason} className="mb-1.5">
          <div className="flex justify-between text-[12px]"><span className={r.reason.startsWith("Win") ? "text-teal-200" : r.reason.startsWith("Loss") ? "text-red-200" : "text-amber-200"}>{r.reason}</span><span className="font-mono text-slate-400">{r.n} · {pc(r.share)}</span></div>
          <div className="h-1.5 rounded bg-line"><div className={`h-1.5 rounded ${r.reason.startsWith("Win") ? "bg-teal-500" : r.reason.startsWith("Loss") ? "bg-red-500" : "bg-amber-500"}`} style={{ width: `${(r.n / max) * 100}%` }} /></div>
        </div>
      ))}
      {!rows?.length && <div className="text-xs text-slate-500">No closed trades yet.</div>}
    </div>
  );
}

function Factor({ f }) {
  const [open, setOpen] = useState(false);
  const w = (s) => (s?.n ? `${s.wins}/${s.n}` : "—");
  return (
    <div className="rounded-lg border border-line bg-panel2/50">
      <button className="flex w-full flex-wrap items-center justify-between gap-2 px-3 py-2 text-left" onClick={() => setOpen(!open)}>
        <span className="text-sm text-slate-100">{f.name}</span>
        <span className="flex items-center gap-2"><span className="font-mono text-[11px] text-slate-400">direction {(f.z_direction ?? 0).toFixed(1)}σ · volatility {(f.z_volatility ?? 0).toFixed(1)}σ</span><Badge v={f.verdict} /></span>
      </button>
      {open && (
        <div className="overflow-x-auto scroll-thin border-t border-line px-2 pb-2">
          <table className="desk-table">
            <thead><tr><th>When</th><th className="num">Recent follow wins</th><th className="num">Recent Plan A wins</th><th className="num">26 mo trades</th><th className="num">26 mo follow targets</th><th className="num">follow R</th><th className="num">26 mo Plan A targets</th><th className="num">Plan A R</th></tr></thead>
            <tbody>
              {f.buckets.map((b) => (
                <tr key={b.bucket}><td>{b.bucket}</td><td className="num">{w(b.site_follow)}</td><td className="num">{w(b.site_fade)}</td><td className="num">{b.hist_follow.n ?? "—"}</td>
                  <td className="num">{pc(b.hist_follow.win)}</td><td className={`num ${ink(b.hist_follow.avg_r)}`}>{rr(b.hist_follow.avg_r)}</td>
                  <td className="num">{pc(b.hist_fade.win)}</td><td className={`num ${ink(b.hist_fade.avg_r)}`}>{rr(b.hist_fade.avg_r)}</td></tr>
              ))}
            </tbody>
          </table>
          <Note tone="plain">Direction = groups where follow and reverse move in opposite ways (the data says which side wins). Volatility = both move the same way (the data only says whether price travels far enough to reach 2R). In standard errors over 26 months: under 2σ ≈ could be luck, 3σ+ ≈ real. R columns are after fees.</Note>
        </div>
      )}
    </div>
  );
}

function Places({ title, rows }) {
  return (
    <div>
      <div className="mb-1.5 text-sm font-semibold text-slate-200">{title}</div>
      <table className="desk-table"><thead><tr><th>Trade</th><th>When</th><th className="num">Trades</th><th className="num">Targets</th><th className="num">After fees</th><th className="num">Before fees</th></tr></thead>
        <tbody>{(rows || []).map((r, i) => <tr key={i}><td>{r.leg}</td><td>{r.factor}: <b>{r.bucket}</b></td><td className="num">{r.n}</td><td className="num">{pc(r.win)}</td><td className={`num ${ink(r.avg_r)}`}>{rr(r.avg_r)}</td><td className={`num ${ink(r.avg_r_gross)}`}>{rr(r.avg_r_gross)}</td></tr>)}</tbody>
      </table>
    </div>
  );
}

export default function StrategyObserver() {
  const { data } = useFetch("/observer", 120000);
  const d = data || {};
  const NAME = { follow: "Follow", fade: "Plan A", switch: "Day-type switch", regime: "Regime", regime_rev: "Regime reverse" };
  return (
    <div className="space-y-4" data-testid="observer-page">
      <PageHead title="Strategy Observer" sub={`Watches every page and journal around the clock. Recent = this site's signals from ${d.recent_from || "—"} (${d.closed_signals ?? "—"} closed). History = the same signals replayed ${d.history_period?.join(" – ") || ""} (${d.history_signals?.toLocaleString() ?? "—"} signals). Updated ${ist(d.computed_at)}.`} />
      {d.error && <Note tone="red">Last update failed: {d.error}</Note>}
      {!d.board && !d.error && <Note>Working it out… the first run takes about two minutes after a restart.</Note>}

      {d.today && (
        <div className={`rounded-xl border px-4 py-3 ${d.today.tone === "green" ? "border-teal-500/60 bg-teal-950/30" : "border-amber-500/60 bg-amber-950/20"}`} data-testid="observer-today">
          <div className="text-[12px] font-semibold text-slate-400">What to do today</div>
          <div className={`text-base font-bold ${d.today.tone === "green" ? "text-teal-200" : "text-amber-200"}`}>{d.today.action}</div>
          <ul className="mt-1.5 list-disc space-y-1 pl-5 text-[13px] text-slate-200">{d.today.lines.map((l, i) => <li key={i}>{l}</li>)}</ul>
        </div>
      )}

      {d.conclusions && (
        <Card title="What the numbers say" sub="Written from the data on this page; it changes as trades close" testid="observer-conclusions">
          <div className="grid gap-2 lg:grid-cols-2">
            {d.conclusions.map((c) => (
              <div key={c.title} className={`rounded-lg border px-3 py-2 ${c.title === "Main finding" ? "border-amber-500/50 bg-amber-950/20 lg:col-span-2" : "border-line bg-panel2/60"}`}>
                <div className="text-[12px] font-semibold text-amber-300">{c.title}</div>
                <div className="text-[13px] leading-relaxed text-slate-200">{c.text}</div>
              </div>
            ))}
          </div>
        </Card>
      )}

      {d.board && (
        <Card title="Which strategy is working" sub="Ranked by profit per trade after fees (in R, 1R = the stop distance), not by win rate, because the books use different targets. 'Likely range' = where the true average probably lies (95%); a book is only proven when the whole range is above zero." testid="observer-board">
          <Leaderboard board={d.board} />
        </Card>
      )}

      {d.whatif && (
        <Card title="What-if lab: stop size, entry fee and hold time (26 months)" sub={d.whatif.any_positive ? "Some settings are positive after fees: see the green cells." : "No setting makes these signals profitable after fees. Compare dollars, not R: a wider stop makes 1R bigger, so R looks better while the dollars lost per trade stay close to the round-trip fee. Limit entries and a longer hold lose less; nothing here creates an edge."} testid="observer-whatif">
          <WhatIf w={d.whatif} />
        </Card>
      )}

      {d.drift && (
        <Card title="Is each strategy still behaving like its history?" sub="Average R over the last 30 trades against the range its 26-month history predicts. Outside the range in either direction is a warning." testid="observer-drift">
          <div className="grid gap-3 lg:grid-cols-2">{d.drift.map((x) => <Drift key={x.key} d={x} />)}</div>
        </Card>
      )}

      {d.register && (
        <Card title="Register of every rule we try" sub="Each rule is judged only on trades after the day it was added. Results before that day helped build it, so they do not count." testid="observer-register">
          <div className="overflow-x-auto scroll-thin">
            <table className="desk-table">
              <thead><tr><th>Rule</th><th>Added (IST)</th><th className="num">Before: trades / R</th><th className="num">Since added: trades</th><th className="num">Profit per trade</th><th className="num">Likely range</th><th>Verdict since added</th><th className="num">26 mo R</th></tr></thead>
              <tbody>{d.register.rules.map((r) => (
                <tr key={r.key}><td>{r.name}<div className="text-[11px] text-slate-500">{r.what}</div></td><td className="font-mono">{r.added}</td>
                  <td className="num">{r.before.n ? `${r.before.n} / ${rr(r.before.avg_r)}` : "—"}</td><td className="num">{r.forward.n}</td>
                  <td className={`num ${ink(r.forward.avg_r)}`}>{rr(r.forward.avg_r)}</td><td className="num text-slate-400">{r.forward.r_lo == null ? "—" : `${rr(r.forward.r_lo)} to ${rr(r.forward.r_hi)}`}</td>
                  <td><Badge v={r.forward.verdict || "No trades yet"} /></td><td className={`num ${ink(r.history?.avg_r)}`}>{rr(r.history?.avg_r)}</td></tr>
              ))}</tbody>
            </table>
          </div>
          <div className="mt-3 text-sm font-semibold text-slate-200">Tested and rejected</div>
          <ul className="mt-1 space-y-1 text-[12.5px] text-slate-300">{d.register.rejected.map((r) => <li key={r.name}><b>{r.name}</b> ({r.tested}): {r.result}</li>)}</ul>
          <Note>Luck check: {d.register.tests} groups of data were tested on 26 months. By chance alone about {d.register.expected_by_luck} would look "significant" at 2σ; {d.register.observed_2sigma} did. {d.register.observed_2sigma <= d.register.expected_by_luck * 1.5 ? "That is close to what luck produces, so treat single good-looking groups with suspicion." : "That is more than luck produces, so some of the data does carry information (still not enough to beat fees)."}</Note>
        </Card>
      )}

      {d.execution && (
        <Card title="Execution quality: real Delta fills vs the paper journal" sub={`${d.execution.n} Auto-Trade trades (${d.execution.closed} closed). Slippage is measured against the planned entry; + means a worse price.`} testid="observer-execution">
          <div className="mb-2 grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-6">
            {[["Avg slippage", rr(d.execution.avg_slip_r)], ["Avg delay vs paper fill", d.execution.avg_delay_s == null ? "—" : `${Math.round(d.execution.avg_delay_s)} s`],
              ["Avg fees", d.execution.avg_fees_r == null ? "—" : `${d.execution.avg_fees_r.toFixed(2)}R`], ["Same result as paper", d.execution.compared ? `${d.execution.same_result} of ${d.execution.compared}` : "—"],
              ["Real vs paper R", d.execution.compared ? `${rr(d.execution.real_r)} vs ${rr(d.execution.paper_r)}` : "—"], ["Missed fills (paper filled)", d.execution.missed.length]].map(([k, v]) => (
              <div key={k} className="rounded-lg border border-line bg-panel2/60 px-3 py-2"><div className="text-[11.5px] text-slate-400">{k}</div><div className="font-mono text-base font-bold text-slate-100">{v}</div></div>
            ))}
          </div>
          <div className="overflow-x-auto scroll-thin">
            <table className="desk-table">
              <thead><tr><th>Signal</th><th>Mode</th><th>Side</th><th className="num">Planned</th><th className="num">Filled</th><th className="num">Slippage</th><th className="num">Delay</th><th>Real exit</th><th>Paper exit</th><th className="num">Real R</th><th className="num">Paper R</th></tr></thead>
              <tbody>{d.execution.trades.map((x) => (
                <tr key={x.signal_id}><td className="font-mono text-[11px]">{x.signal_id}</td><td>{x.mode}</td><td>{x.side}</td><td className="num">{x.planned}</td><td className="num">{x.filled}</td>
                  <td className={`num ${ink(-(x.slip_r || 0))}`}>{x.slip_usd == null ? "—" : `${x.slip_usd > 0 ? "+" : ""}${x.slip_usd} $`}</td><td className="num">{x.delay_s == null ? "—" : `${x.delay_s}s`}</td>
                  <td>{x.exit || x.status}</td><td>{x.paper_result || "—"}</td><td className={`num ${ink(x.r)}`}>{rr(x.r)}</td><td className={`num ${ink(x.paper_r)}`}>{rr(x.paper_r)}</td></tr>
              ))}</tbody>
            </table>
          </div>
        </Card>
      )}

      {d.reasons && (
        <Card title="Why trades hit target, why they hit stop" sub="From the 1-minute path of every closed recent trade">
          <div className="grid gap-5 md:grid-cols-3">
            <Reasons title="Trade Journal (follow)" rows={d.reasons.follow} />
            <Reasons title="Plan A (reverse)" rows={d.reasons.fade} />
            <Reasons title="Day-type switch" rows={d.reasons.switch} />
          </div>
        </Card>
      )}

      {d.factors && (
        <Card title="Which data helps, which is useless" sub="Each piece of data the pages show at the signal, tested on 26 months and on the recent trades. Click one for the detail." testid="observer-factors">
          <div className="grid gap-2 lg:grid-cols-2">{d.factors.map((f) => <Factor key={f.key} f={f} />)}</div>
        </Card>
      )}

      {d.extremes && (
        <Card title="Where to get in, and where not to" sub="Conditions with 150+ trades over 26 months, ranked by average result after fees">
          <div className="grid gap-5 lg:grid-cols-2"><Places title="Best conditions" rows={d.extremes.best} /><Places title="Worst conditions" rows={d.extremes.worst} /></div>
        </Card>
      )}

      {d.pages && (
        <Card title="Every page, observed" sub="What each page is for, what we have measured, and the verdict" testid="observer-pages">
          <div className="grid gap-2 md:grid-cols-2 xl:grid-cols-3">
            {d.pages.map((p) => (
              <div key={p.page} className="rounded-lg border border-line bg-panel2/60 px-3 py-2.5">
                <div className="flex items-center justify-between gap-2"><Link to={p.to} className="text-sm font-semibold text-amber-300 hover:underline">{p.page}</Link></div>
                <div className="text-[12px] text-slate-400">{p.role}</div>
                <div className="mt-1 text-[12.5px] font-semibold text-slate-100">{p.verdict}</div>
                <div className="mt-1 text-[12px] leading-relaxed text-slate-300">{p.evidence}</div>
              </div>
            ))}
          </div>
        </Card>
      )}

      {d.forward_pages && (
        <Card title="Data that will help in future: recorded at every signal" sub={`Every page's reading is saved the moment a signal fires (${d.recorded_snapshots ?? 0} signals recorded so far). Once 50+ trades close, this shows which page really points to winners.`}>
          <div className="overflow-x-auto scroll-thin">
            <table className="desk-table">
              <thead><tr><th>Page reading</th><th className="num">Agreed: follow wins</th><th className="num">Against: follow wins</th><th className="num">Neutral: follow wins</th><th className="num">Recorded</th></tr></thead>
              <tbody>{d.forward_pages.map((p) => (
                <tr key={p.key}><td>{p.name}</td>{["agreed", "against", "neutral"].map((k) => <td key={k} className="num">{p[k].n ? `${p[k].follow_wins}/${p[k].n}` : "—"}</td>)}<td className="num">{p.n}</td></tr>
              ))}</tbody>
            </table>
          </div>
          {d.support_checks?.length > 0 && (
            <div className="mt-3 overflow-x-auto scroll-thin">
              <div className="mb-1 text-sm font-semibold text-slate-200">Day-type switch support checks (recorded since 16 Nov)</div>
              <table className="desk-table"><thead><tr><th>Check</th><th className="num">Passed: switch wins</th><th className="num">Failed: switch wins</th></tr></thead>
                <tbody>{d.support_checks.map((c) => <tr key={c.key}><td>{c.name}</td><td className="num">{c.pass[0] ? `${c.pass[1]}/${c.pass[0]}` : "—"}</td><td className="num">{c.fail[0] ? `${c.fail[1]}/${c.fail[0]}` : "—"}</td></tr>)}</tbody></table>
            </div>
          )}
        </Card>
      )}

      {d.history_by_market && (
        <Card title="Does it work in every market? (26 months)" sub="Average result per trade after fees, by BTC's 30-day move, and in the first vs second half of the period">
          <div className="overflow-x-auto scroll-thin">
            <table className="desk-table">
              <thead><tr><th>Book</th>{Object.keys(d.history_by_market).map((m) => <th key={m} className="num">{m}</th>)}<th className="num">First half</th><th className="num">Second half</th></tr></thead>
              <tbody>{Object.keys(NAME).map((k) => (
                <tr key={k}><td>{NAME[k]}</td>{Object.keys(d.history_by_market).map((m) => { const s = d.history_by_market[m][k] || {}; return <td key={m} className={`num ${ink(s.avg_r)}`}>{rr(s.avg_r)} <span className="text-slate-500">({pc(s.win)})</span></td>; })}
                  {["first", "second"].map((h) => { const s = d.history_by_half?.[h]?.[k] || {}; return <td key={h} className={`num ${ink(s.avg_r)}`}>{rr(s.avg_r)}</td>; })}</tr>
              ))}</tbody>
            </table>
          </div>
          {d.regime_rules_history && (
            <div className="mt-3 overflow-x-auto scroll-thin">
              <div className="mb-1 text-sm font-semibold text-slate-200">Regime setups over 26 months</div>
              <table className="desk-table"><thead><tr><th>Setup</th><th className="num">Trades</th><th className="num">Targets</th><th className="num">After fees</th><th className="num">Before fees</th></tr></thead>
                <tbody>{Object.entries(d.regime_rules_history).map(([k, s]) => <tr key={k}><td>{k}</td><td className="num">{s.n}</td><td className="num">{pc(s.win)}</td><td className={`num ${ink(s.avg_r)}`}>{rr(s.avg_r)}</td><td className={`num ${ink(s.avg_r_gross)}`}>{rr(s.avg_r_gross)}</td></tr>)}</tbody></table>
            </div>
          )}
        </Card>
      )}

      {d.log?.length > 0 && (
        <Card title="24/7 watch log" sub="One line per day: each book's recent trades, win rate and net, as the observer saw it">
          <div className="overflow-x-auto scroll-thin">
            <table className="desk-table"><thead><tr><th>Date</th>{(d.log[0].board || []).map((b) => <th key={b.key} className="num">{NAME[b.key] || "Auto-Trade"}</th>)}</tr></thead>
              <tbody>{d.log.map((l) => <tr key={l._id}><td className="font-mono">{l._id}</td>{(l.board || []).map((b) => <td key={b.key} className={`num ${ink(b.net)}`}>{b.n ? `${pc(b.win)} · ${signedUsd(b.net, 0)}` : "—"}</td>)}</tr>)}</tbody></table>
          </div>
        </Card>
      )}
    </div>
  );
}
