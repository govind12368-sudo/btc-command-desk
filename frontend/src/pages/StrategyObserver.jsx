import React, { useState } from "react";
import { Link } from "react-router-dom";
import { useFetch } from "@/hooks/useFetch";
import { Card, PageHead, Note } from "@/components/desk/ui";
import { ist, signedUsd } from "@/lib/fmt";

const pc = (v, d = 0) => (v == null ? "—" : `${(v * 100).toFixed(d)}%`);
const rr = (v) => (v == null ? "—" : `${v > 0 ? "+" : ""}${v.toFixed(2)}R`);
const ink = (v) => (v == null ? "text-slate-400" : v > 0 ? "text-teal-300" : v < 0 ? "text-red-300" : "text-slate-300");
const VERDICT_TONE = (v = "") =>
  v.startsWith("Proven") ? "border-teal-500/50 text-teal-200 bg-teal-950/30" : v.startsWith("Above") ? "border-sky-500/50 text-sky-200 bg-sky-950/30"
    : v.startsWith("Losing") ? "border-red-500/50 text-red-200 bg-red-950/30" : v.startsWith("Too few") ? "border-slate-600 text-slate-300"
      : v.startsWith("Helps") ? "border-teal-500/50 text-teal-200 bg-teal-950/30" : v.startsWith("Small") ? "border-amber-500/50 text-amber-200 bg-amber-950/20"
        : "border-slate-600 text-slate-300";
const Badge = ({ v }) => <span className={`inline-block rounded border px-1.5 py-0.5 text-[11px] ${VERDICT_TONE(v)}`}>{v || "—"}</span>;

function Leaderboard({ board }) {
  return (
    <div className="overflow-x-auto scroll-thin">
      <table className="desk-table">
        <thead>
          <tr><th>Strategy / journal</th><th className="num">Trades</th><th className="num">Win rate</th><th className="num">95% range</th><th className="num">Break-even</th>
            <th className="num">Net after fees</th><th className="num">Avg R</th><th>Verdict (recent)</th>
            <th className="num">26 mo trades</th><th className="num">26 mo targets</th><th className="num">26 mo after fees</th><th className="num">26 mo before fees</th></tr>
        </thead>
        <tbody>
          {board.map((b) => {
            const s = b.recent || {}; const h = b.history || {};
            return (
              <tr key={b.key}>
                <td><Link className="text-amber-300 hover:underline" to={b.to}>{b.name}</Link></td>
                <td className="num">{s.n ?? 0}</td><td className="num font-semibold">{pc(s.win)}</td>
                <td className="num text-slate-400">{s.win_lo == null ? "—" : `${pc(s.win_lo)}–${pc(s.win_hi)}`}</td>
                <td className="num">{pc(s.breakeven)}</td><td className={`num ${ink(s.net)}`}>{s.n ? signedUsd(s.net) : "—"}</td><td className={`num ${ink(s.avg_r)}`}>{rr(s.avg_r)}</td>
                <td><Badge v={s.verdict} /></td>
                <td className="num">{h.n?.toLocaleString() ?? "—"}</td><td className="num">{pc(h.win)}</td>
                <td className={`num ${ink(h.avg_r)}`}>{rr(h.avg_r)}</td><td className={`num ${ink(h.avg_r_gross)}`}>{rr(h.avg_r_gross)}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
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
          <div className="h-1.5 rounded bg-[#1e2a4a]"><div className={`h-1.5 rounded ${r.reason.startsWith("Win") ? "bg-teal-500" : r.reason.startsWith("Loss") ? "bg-red-500" : "bg-amber-500"}`} style={{ width: `${(r.n / max) * 100}%` }} /></div>
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
    <div className="rounded-lg border border-[#1e2a4a] bg-[#0f172a]/50">
      <button className="flex w-full flex-wrap items-center justify-between gap-2 px-3 py-2 text-left" onClick={() => setOpen(!open)}>
        <span className="text-sm text-slate-100">{f.name}</span>
        <span className="flex items-center gap-2"><span className="font-mono text-[11px] text-slate-400">effect {f.zmax.toFixed(1)}σ</span><Badge v={f.verdict} /></span>
      </button>
      {open && (
        <div className="overflow-x-auto scroll-thin border-t border-[#1e2a4a] px-2 pb-2">
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
          <p className="mt-1 text-[11px] text-slate-500">Effect = how far the best or worst group's win rate sits from all other signals, in standard errors over 26 months (2σ ≈ could be luck, 3σ+ ≈ real). R columns are after fees.</p>
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

      {d.conclusions && (
        <Card title="What the numbers say" sub="Written from the data on this page; it changes as trades close" testid="observer-conclusions">
          <div className="grid gap-2 lg:grid-cols-2">
            {d.conclusions.map((c) => (
              <div key={c.title} className={`rounded-lg border px-3 py-2 ${c.title === "Main finding" ? "border-amber-500/50 bg-amber-950/20 lg:col-span-2" : "border-[#1e2a4a] bg-[#0f172a]/60"}`}>
                <div className="text-[12px] font-semibold text-amber-300">{c.title}</div>
                <div className="text-[13px] leading-relaxed text-slate-200">{c.text}</div>
              </div>
            ))}
          </div>
        </Card>
      )}

      {d.board && (
        <Card title="Which strategy is working" sub="Sorted by recent win rate. Break-even = the win rate each book needs with its own average win and loss. '95% range' = where the true win rate probably lies; a book is only proven when the whole range sits above break-even." testid="observer-board">
          <Leaderboard board={d.board} />
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
              <div key={p.page} className="rounded-lg border border-[#1e2a4a] bg-[#0f172a]/60 px-3 py-2.5">
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
