import React, { useState } from "react";
import { Link } from "react-router-dom";
import { Card, Note } from "@/components/desk/ui";
import { n, ist, ago, signedUsd } from "@/lib/fmt";
import { PeriodSwitch, Stat } from "@/components/desk/Books";

const NAME = { BULLISH: "Bullish", "LEAN BULLISH": "Lean bullish", NEUTRAL: "Neutral", "LEAN BEARISH": "Lean bearish", BEARISH: "Bearish" };
const INK = { BULLISH: "text-teal-300", "LEAN BULLISH": "text-teal-300", NEUTRAL: "text-slate-300", "LEAN BEARISH": "text-red-300", BEARISH: "text-red-300" };
const PILL = { BULLISH: "border-teal-500/40 bg-teal-500/10 text-teal-300", "LEAN BULLISH": "border-teal-500/30 bg-teal-500/5 text-teal-300", NEUTRAL: "border-slate-500/40 bg-slate-500/10 text-slate-300",
  "LEAN BEARISH": "border-red-500/30 bg-red-500/5 text-red-300", BEARISH: "border-red-500/40 bg-red-500/10 text-red-300" };
const SHORT = { scanner: "Scanner", pillars: "Pillars", intel: "Intelligence", news: "News" };
const signed = (v, d = 0) => (v == null ? "—" : `${v > 0 ? "+" : v < 0 ? "−" : ""}${Math.abs(v).toFixed(d)}`);
const Tone = ({ label }) => <span className={`inline-flex whitespace-nowrap rounded-full border px-2 py-0.5 text-[11px] font-semibold ${PILL[label] || "border-slate-600 text-slate-500"}`}>{NAME[label] || "No data"}</span>;
const open = "text-[12px] font-semibold text-amber-400 underline-offset-2 hover:text-amber-300 hover:underline";

// A bar that grows left (bearish) or right (bullish) from the middle. value and max share one unit.
function Diverge({ value, max = 100, height = 10, label }) {
  const w = value == null ? 0 : Math.min(Math.abs(value) / max, 1) * 50;
  return (
    <div className="relative w-full rounded bg-slate-700/40" style={{ height }} role="img" aria-label={label}>
      {value != null && value !== 0 && <div className={`absolute top-0 h-full ${value > 0 ? "rounded-r bg-teal-400" : "rounded-l bg-red-400"}`} style={value > 0 ? { left: "50%", width: `${w}%` } : { right: "50%", width: `${w}%` }} />}
      <div className="absolute left-1/2 top-[-2px] w-px bg-slate-300" style={{ height: height + 4 }} />
    </div>
  );
}

const SUP = { STRONG: ["Strong", "border-teal-500/40 bg-teal-500/10 text-teal-300"], MEDIUM: ["Medium", "border-amber-500/40 bg-amber-500/10 text-amber-300"], WEAK: ["Weak", "border-slate-500/40 bg-slate-500/10 text-slate-300"] };
const FIT = {
  AGREES: ["Market read agrees", "border-teal-500/40 bg-teal-500/10 text-teal-300"], AGAINST: ["Market read is against it", "border-red-500/40 bg-red-500/10 text-red-300"],
  MIXED: ["Market read is mixed", "border-slate-500/40 bg-slate-500/10 text-slate-300"], NO_SIGNAL: ["No signal now", "border-slate-500/40 bg-slate-500/10 text-slate-300"],
  UNKNOWN: ["No data yet", "border-slate-600 text-slate-500"],
};

function Read({ title, forPlan, r, plan }) {
  const fit = FIT[plan?.fit] || FIT.UNKNOWN;
  return (
    <div className="flex flex-col rounded-lg border border-[#1e2a4a] bg-[#0f172a]/60 p-4" data-testid={`read-${r.view}`}>
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h4 className="text-[13.5px] font-semibold text-slate-100">{title}</h4>
        <span className="text-[11.5px] text-slate-500">{forPlan}</span>
      </div>
      <div className="mt-1 flex flex-wrap items-baseline gap-x-3">
        <span className={`font-mono text-4xl font-bold ${INK[r.label] || "text-slate-500"}`} data-testid={`read-${r.view}-score`}>{signed(r.score)}</span>
        <span className={`text-lg font-bold ${INK[r.label] || "text-slate-500"}`}>{NAME[r.label] || "No data"}</span>
        <span className="text-[11.5px] text-slate-500">scale −100 to +100</span>
      </div>
      <div className="mt-2"><Diverge value={r.score} label={`Market read ${signed(r.score)} on a scale from minus 100 to plus 100`} /></div>
      <div className="mt-1 flex justify-between text-[10.5px] text-slate-500"><span>−100 bearish</span><span>0</span><span>bullish +100</span></div>

      <div className="mt-3 flex h-6 gap-[2px]" role="img" aria-label={`Weight of each module: ${r.rows.map((x) => `${x.name} ${x.weight} percent`).join(", ")}`}>
        {r.rows.map((x) => <div key={x.key} className={`flex items-center justify-center overflow-hidden rounded-sm text-[10.5px] font-semibold ${x.reading == null ? "bg-slate-800 text-slate-600" : "bg-slate-600/70 text-slate-100"}`} style={{ width: `${x.weight}%` }} title={`${x.name}: weight ${x.weight}%`}>{x.weight >= 20 ? `${SHORT[x.key]} ${x.weight}%` : SHORT[x.key]}</div>)}
      </div>
      <div className="mt-2 overflow-x-auto scroll-thin">
        <table className="desk-table">
          <thead><tr><th>Module</th><th className="num">Weight</th><th>Reading</th><th className="num">Points</th></tr></thead>
          <tbody>{r.rows.map((x) => (
            <tr key={x.key}>
              <td className="min-w-[190px]"><Link to={x.to} className="font-semibold text-slate-100 underline decoration-slate-600 underline-offset-2 hover:decoration-amber-400">{x.name}</Link><div className="text-[11.5px] leading-snug text-slate-400">{x.detail}</div></td>
              <td className="num font-mono">{x.weight}%</td>
              <td className="min-w-[120px]"><div className={`text-[12px] font-semibold ${INK[x.reading_label] || "text-slate-500"}`}>{NAME[x.reading_label] || "No data"} {x.reading != null && <span className="font-mono font-normal text-slate-400">{signed(x.reading, 2)}</span>}</div>
                <div className="mt-1"><Diverge value={x.reading} max={1} height={6} label={`${x.name} reading ${signed(x.reading, 2)} on a scale from minus 1 to plus 1`} /></div></td>
              <td className={`num font-mono font-semibold ${x.points == null ? "text-slate-500" : x.points > 0 ? "text-teal-300" : x.points < 0 ? "text-red-300" : "text-slate-300"}`}>{signed(x.points)}</td>
            </tr>))}
            <tr><td className="font-semibold text-slate-100">Market read</td><td className="num font-mono">{r.coverage}%</td><td className="text-[11.5px] text-slate-500">{r.coverage < 100 ? "missing modules left out" : "all modules have data"}</td><td className={`num font-mono font-bold ${INK[r.label] || "text-slate-500"}`}>{signed(r.score)}</td></tr>
          </tbody>
        </table>
      </div>

      {plan && <div className="mt-3 flex-1 rounded-md border border-[#1e2a4a] bg-[#0b132b]/60 p-3" data-testid={`plan-link-${r.view}`}>
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div className="text-[13px] font-semibold text-slate-100">{plan.name}: <span className={plan.status === "BUY" ? "text-teal-300" : plan.status === "SELL" ? "text-red-300" : "text-amber-300"}>{plan.status || "—"}</span>
            {plan.direction && <span className="text-slate-300"> · {plan.direction}</span>}{plan.total ? <span className="font-mono text-[12px] font-normal text-slate-400"> · {plan.passed}/{plan.total} checks</span> : null}</div>
          <span className={`rounded-full border px-2 py-0.5 text-[11px] font-semibold ${fit[1]}`}>{fit[0]}</span>
        </div>
        <p className="mt-1.5 text-[12.5px] leading-snug text-slate-300">{plan.text}</p>
        {plan.playbook && <p className="mt-1.5 text-[12.5px] leading-snug text-slate-300">Day type now: <Link to={plan.playbook.to} className="font-semibold text-slate-100 underline decoration-slate-600 underline-offset-2 hover:decoration-amber-400">{plan.playbook.day_type}</Link>. {plan.playbook.text}</p>}
        {plan.levels && <p className="mt-1 font-mono text-[12px] text-slate-300">Entry {n(plan.levels.entry, 1)} · Stop {n(plan.levels.stop, 1)} · Target {n(plan.levels.target, 1)}</p>}
        <Link to={plan.to} className={`mt-2 inline-block ${open}`} data-testid={`open-${r.view}-plan`}>Open the {plan.name.toLowerCase()}</Link>
      </div>}
    </div>
  );
}

export function MarketRead({ d }) {
  if (!d) return <Card title="Market read and weightage" testid="market-read"><p className="text-[12.5px] text-slate-400">Loading the summary of every module…</p></Card>;
  return (
    <Card title="Market read and weightage" testid="market-read" sub={`All four modules in one number, and how it sits against each trade plan · updated ${ago(d.updated)}`}>
      {d.event_risk && <div className="mb-3"><Note tone="amber">{d.event_risk}</Note></div>}
      <div className="grid gap-3 xl:grid-cols-2">
        <Read title="Intraday read" forPlan="used with the scalping plan" r={d.reads.intraday} plan={d.plans[0]} />
        <Read title="Swing read" forPlan="used with the swing plan" r={d.reads.swing} plan={d.plans[1]} />
      </div>
      <p className="mt-3 text-[11.5px] leading-snug text-slate-500">How it is worked out: each module gives a reading from −1 (bearish) to +1 (bullish); points = reading × weight; the market read is the total. {d.reads.intraday.rule} {d.note}</p>
    </Card>
  );
}

function PillarRows({ rows }) {
  return (
    <ul className="space-y-1.5">{rows.map((p) => (
      <li key={p.key} className="grid grid-cols-[84px_1fr_58px] items-center gap-2 text-[12px]">
        <span className="text-slate-300">{p.name}</span>
        {p.valid ? <Diverge value={p.score} max={p.max} height={6} label={`${p.name} score ${signed(p.score, 1)}`} /> : <div className="h-[6px] rounded bg-[repeating-linear-gradient(45deg,#334155_0_4px,transparent_4px_8px)]" title="No data" />}
        <span className={`text-right font-mono ${!p.valid ? "text-slate-500" : p.score > 0 ? "text-teal-300" : p.score < 0 ? "text-red-300" : "text-slate-300"}`}>{p.valid ? signed(p.score, 1) : "no data"}</span>
      </li>))}
    </ul>
  );
}

function ModuleCard({ c }) {
  return (
    <section className="desk-card flex flex-col p-4" data-testid={`module-${c.key}`}>
      <header className="flex items-start justify-between gap-2">
        <h4 className="text-[14px] font-semibold text-slate-100">{c.title}</h4>
        <Tone label={c.ok ? c.tone : null} />
      </header>
      <p className="mt-1 text-[13.5px] font-semibold leading-snug text-slate-50">{c.ok ? c.headline : "No data yet"}</p>
      <div className="mt-2 flex-1 space-y-2">
        {c.pillars && <PillarRows rows={c.pillars} />}
        <dl className="space-y-1">{c.lines.map((l) => <div key={l.k} className="grid grid-cols-[84px_1fr] gap-2 text-[12px] leading-snug"><dt className="text-slate-500">{l.k}</dt><dd className="text-slate-200">{l.v}</dd></div>)}</dl>
        {c.events && (c.events.length ? <ul className="space-y-1">{c.events.map((e) => <li key={e.title + e.time} className="grid grid-cols-[84px_1fr] gap-2 text-[12px] leading-snug">
          <span className={e.impact === "HIGH" || e.impact === "CRITICAL" ? "font-semibold text-amber-300" : "text-slate-500"}>{e.hours < 1 ? "within 1h" : e.hours < 48 ? `in ${Math.round(e.hours)}h` : `in ${Math.round(e.hours / 24)}d`}</span>
          <span className="text-slate-200">{e.title}<span className="text-slate-500"> · {ist(e.time)}</span></span></li>)}</ul>
          : <p className="text-[12px] text-slate-500">No scheduled US data release in the next 3 days.</p>)}
        {c.headlines?.map((h) => <a key={h.link} href={h.link} target="_blank" rel="noopener noreferrer" className="block text-[12px] leading-snug text-slate-300 underline decoration-slate-700 underline-offset-2 hover:decoration-amber-400">{h.title}</a>)}
      </div>
      <Link to={c.to} className={`mt-3 ${open}`} data-testid={`open-${c.key}`}>Open {c.title}</Link>
    </section>
  );
}

export function ModuleCards({ d }) {
  if (!d) return null;
  return (
    <div data-testid="module-cards">
      <h3 className="mb-2 text-[15px] font-semibold tracking-tight text-slate-100">What each page says now</h3>
      <div className="grid gap-3 md:grid-cols-2 2xl:grid-cols-4">{d.cards.map((c) => <ModuleCard key={c.key} c={c} />)}</div>
    </div>
  );
}

// The Day-type switch is the plan Auto-Trade follows, so it leads; the two fixed ways are kept below for comparison only.
export function SwitchFocus({ d }) {
  const [period, setPeriod] = useState("since");
  const b = d?.books;
  if (!b) return null;
  const rows = b[period] || [];
  const sw = rows.find((r) => r.key === "switch") || {};
  const others = rows.filter((r) => r.key !== "switch");
  const tr = b.switch_trades?.[period] || [];
  const a = d.auto;
  const ink = (v) => (v == null ? "text-slate-500" : v > 0 ? "text-teal-300" : v < 0 ? "text-red-300" : "text-slate-300");
  const px = (v) => (v == null ? "—" : n(v, 1));
  const total = tr.reduce((x, t) => x + (t.net || 0), 0);
  return (
    <Card title="Day-type switch: the plan Auto-Trade follows" testid="switch-focus" sub="Range day: Plan A (reverse the signal). Trend day and the signal goes with the trend: follow it. Anything else: no trade."
      right={<PeriodSwitch value={period} onChange={setPeriod} sinceDate={b.since_date} />}>
      <div className="grid grid-cols-2 gap-2 sm:grid-cols-3 xl:grid-cols-6" data-testid="switch-stats">
        <Stat label="Net result (est.)" value={signedUsd(sw.net)} tone={ink(sw.net)} />
        <Stat label="Trades taken" value={sw.taken ?? "—"} sub={sw.open ? `${sw.open} still open` : null} />
        <Stat label="Signals it stood aside on" value={sw.stood_aside ?? "—"} sub={`of ${sw.signals ?? 0} signals`} />
        <Stat label="Win rate" value={sw.win_rate == null ? "—" : `${Math.round(sw.win_rate * 100)}%`} />
        <Stat label="Target / Stop / 6h exit" value={`${sw.target ?? 0} / ${sw.stop ?? 0} / ${sw.time ?? 0}`} />
        <Stat label="Worst drawdown" value={signedUsd(sw.max_drawdown)} tone="text-red-300" />
      </div>
      {a && <p className="mt-3 rounded-md border border-[#1e2a4a] bg-[#0b132b]/60 px-3 py-2 text-[12.5px] text-slate-300" data-testid="switch-auto">
        <b className="text-slate-100">Auto-Trade:</b> {a.mode === "PAPER" ? "paper mode" : a.mode === "LIVE" ? "LIVE mode" : "testnet mode"} · {a.status} · {a.day_type_only ? "takes Day-type switch trades only" : "not set to Day-type switch"} · today <span className={`font-mono ${ink(a.today)}`}>{signedUsd(a.today)}</span>
        {a.open_trade && <> · in a trade: {a.open_trade.bot_dir} from {px(a.open_trade.entry)}, stop {px(a.open_trade.stop)}, target {px(a.open_trade.target)}</>}
        {" · "}<Link to="/autotrade" className={open}>Open Auto-Trade</Link>
      </p>}
      {d.support_now && <div className="mt-4" data-testid="switch-support-now">
        <h4 className="mb-1 text-[13px] font-semibold text-slate-100">Support if a scalp signal fires now</h4>
        <div className="grid gap-2 md:grid-cols-2">{d.support_now.map((x) => (
          <div key={x.signal} className="rounded-md border border-[#1e2a4a] bg-[#0b132b]/60 px-3 py-2">
            <div className="flex flex-wrap items-center justify-between gap-2 text-[12.5px]">
              <span className="text-slate-300">A {x.signal} signal: {x.plan === "STAND_ASIDE" ? <b className="text-slate-100">no trade (stand aside)</b> : <><b className="text-slate-100">{x.plan === "PLAN_A" ? "Plan A (reverse)" : "follow it"}</b>, trade <b className={x.trade_dir === "LONG" ? "text-teal-300" : "text-red-300"}>{x.trade_dir}</b></>}</span>
              {x.level && <span className={`rounded-full border px-2 py-0.5 text-[11px] font-semibold ${SUP[x.level][1]}`}>{SUP[x.level][0]} support · {x.passed} of {x.of}</span>}
            </div>
            {x.checks.length > 0 && <ul className="mt-1.5 space-y-0.5 text-[12px] leading-snug">{x.checks.map((c) => <li key={c.key} className={c.ok ? "text-slate-200" : "text-slate-500"}>{c.ok == null ? "—" : c.ok ? "✓" : "✗"} <b>{c.name}.</b> {c.text}</li>)}</ul>}
          </div>))}
        </div>
      </div>}
      {(() => {
        const sp = b.support_split?.[period];
        if (!sp) return null;
        return (
          <div className="mt-4" data-testid="switch-support-split">
            <h4 className="mb-1 text-[13px] font-semibold text-slate-100">Results by support level</h4>
            <div className="overflow-x-auto scroll-thin">
              <table className="desk-table">
                <thead><tr><th>Support when the signal fired</th><th className="num">Trades</th><th className="num">Closed</th><th className="num">Won</th><th className="num">Win rate</th><th className="num">Net result</th></tr></thead>
                <tbody>{sp.rows.map((r) => (
                  <tr key={r.level} className={r.trades ? "" : "text-slate-500"}>
                    <td>{r.name}</td><td className="num font-mono">{r.trades}</td><td className="num font-mono">{r.closed}</td><td className="num font-mono">{r.wins}</td>
                    <td className="num font-mono">{r.win_rate == null ? "—" : `${Math.round(r.win_rate * 100)}%`}</td><td className={`num font-mono font-semibold ${r.trades ? ink(r.net) : "text-slate-500"}`}>{r.trades ? signedUsd(r.net) : "—"}</td>
                  </tr>))}</tbody>
              </table>
            </div>
            <p className="mt-1 text-[11.5px] leading-snug text-slate-500">{sp.ready ? `${sp.recorded} recorded trades.` : `Collecting: ${sp.recorded} of ${sp.need} recorded trades.`} {sp.note} Support is saved once, at the moment the signal fires; trades from before this was added are "not recorded".</p>
          </div>
        );
      })()}
      <div className="mt-4" data-testid="switch-trades">
        <h4 className="mb-1 text-[13px] font-semibold text-slate-100">The {tr.length} trade{tr.length === 1 ? "" : "s"} it took</h4>
        {tr.length ? <div className="overflow-x-auto scroll-thin">
          <table className="desk-table">
            <thead><tr><th className="num">#</th><th>Day type</th><th>Trade taken</th><th>Entry time (IST)</th><th className="num">Entry</th><th className="num">Stop</th><th className="num">Target</th><th>Exit time (IST)</th><th className="num">Exit price</th><th>Result</th><th className="num">Net result</th></tr></thead>
            <tbody>{tr.map((t, i) => (
              <tr key={t.signal_id}>
                <td className="num font-mono text-slate-500">{i + 1}</td><td className="whitespace-nowrap">{t.day_type || "—"}</td>
                <td className="whitespace-nowrap">{t.way} · <b className={t.direction === "LONG" ? "text-teal-300" : "text-red-300"}>{t.direction}</b></td>
                <td className="whitespace-nowrap font-mono text-[12px]">{t.entry_time || "—"}</td><td className="num font-mono">{px(t.entry)}</td><td className="num font-mono">{px(t.stop)}</td><td className="num font-mono">{px(t.target)}</td>
                <td className="whitespace-nowrap font-mono text-[12px]">{t.exit_time || "still open"}</td><td className="num font-mono">{px(t.exit_price)}</td>
                <td className={`whitespace-nowrap font-semibold ${t.result === "TARGET" ? "text-teal-300" : t.result === "STOP" ? "text-red-300" : "text-amber-300"}`}>{{ TARGET: "Target hit", STOP: "Stop hit", TIME: "6h exit", OPEN: "Open" }[t.result] || t.result}</td>
                <td className={`num font-mono font-semibold ${ink(t.net)}`}>{signedUsd(t.net)}</td>
              </tr>))}
              <tr><td /><td colSpan={9} className="font-semibold text-slate-100">Total</td><td className={`num font-mono font-bold ${ink(total)}`}>{signedUsd(total)}</td></tr>
            </tbody>
          </table>
        </div> : <p className="text-[12.5px] text-slate-500">No trades taken in this period.</p>}
      </div>
      <p className="mt-3 text-[12px] text-slate-400" data-testid="switch-compare">For comparison only, not traded by Auto-Trade: {others.map((r, i) => <span key={r.key}>{i > 0 && " · "}<Link to={r.to} className="underline decoration-slate-600 underline-offset-2">{r.name}</Link> <span className={`font-mono ${ink(r.net)}`}>{signedUsd(r.net)}</span> on {r.taken ?? 0} trades</span>)}.</p>
      <p className="mt-1 text-[11.5px] leading-snug text-slate-500">{b.note} These are the Trade Journal's simulated results on the same signals; Auto-Trade's own fills are on the Auto-Trade page.</p>
    </Card>
  );
}
