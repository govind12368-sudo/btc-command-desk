import React from "react";
import { Card } from "@/components/desk/ui";
import { n, usd, ist } from "@/lib/fmt";
import { LineChart, VIZ } from "@/components/desk/charts";

const TYPE_TONE = {
  TREND_UP: "border-teal-500/40 bg-teal-500/10 text-teal-300", TREND_DOWN: "border-red-500/40 bg-red-500/10 text-red-300",
  RANGE: "border-sky-500/40 bg-sky-500/10 text-sky-300", MIXED: "border-slate-500/40 bg-slate-500/10 text-slate-300", NA: "border-slate-700 text-slate-500",
};
export const TYPE_NAME = { TREND_UP: "Trend ▲", TREND_DOWN: "Trend ▼", RANGE: "Range", MIXED: "Unclear", NA: "—" };
export const PLAYBOOK_NAME = { FOLLOW: "Follow signal", PLAN_A: "Plan A (reverse)", STAND_ASIDE: "Stand aside" };
export const BOOK_NAME = { follow: "Follow the signal", plan_a: "Plan A (reverse 1:2)", switch: "Day-type switch" };
const pnl = (v) => ((v || 0) >= 0 ? "text-teal-300" : "text-red-300");
const r2 = (v) => (v == null ? "—" : `${v > 0 ? "+" : ""}${n(v, 2)}R`);

export function DayChip({ type, title }) {
  const t = type || "NA";
  return <span title={title} data-testid="day-chip" className={`inline-flex whitespace-nowrap rounded border px-2 py-0.5 text-[11px] font-semibold ${TYPE_TONE[t] || TYPE_TONE.NA}`}>{TYPE_NAME[t] || t}</span>;
}

export function Stat({ label, value, tone = "text-slate-50", sub }) {
  return (
    <div className="rounded-lg border border-[#1e2a4a] bg-[#0f172a]/60 px-3 py-2.5">
      <div className="text-[11.5px] text-slate-400">{label}</div>
      <div className={`font-mono text-lg font-bold ${tone}`}>{value}</div>
      {sub && <div className="text-[11px] text-slate-500">{sub}</div>}
    </div>
  );
}

/** Same eight figures and curve as the Plan A page, for any book. */
export function BookPanel({ book, title, sub, testid }) {
  const b = book || {};
  return (
    <div className="space-y-3" data-testid={testid}>
      <div className="grid grid-cols-2 gap-2 sm:grid-cols-4 lg:grid-cols-8">
        <Stat label="Signals" value={b.signals ?? "—"} />
        <Stat label="Trades taken" value={b.taken ?? "—"} />
        <Stat label={b.stood_aside ? "Skipped (busy / aside)" : "Skipped (busy)"} value={b.stood_aside ? `${b.skipped} / ${b.stood_aside}` : b.skipped ?? "—"} />
        <Stat label="Still open" value={b.open ?? "—"} tone="text-sky-300" />
        <Stat label="Target / Stop / 6h" value={`${b.target ?? 0} / ${b.stop ?? 0} / ${b.time ?? 0}`} />
        <Stat label="Win rate" value={b.win_rate == null ? "—" : `${Math.round(b.win_rate * 100)}%`} />
        <Stat label="Net P&L (est.)" value={usd(b.net)} tone={pnl(b.net)} />
        <Stat label="Worst drawdown" value={usd(b.max_drawdown)} tone="text-red-300" />
      </div>
      {b.curve?.length > 1 ? (
        <Card title={title || "Cumulative net P&L"} sub={sub}>
          <LineChart series={[{ name: "Net P&L $", color: VIZ.s1, points: b.curve }]} xFmt={(v) => v} yFmt={(v) => n(v, 0)} unit=" $" height={160} />
        </Card>
      ) : <p className="rounded-lg border border-[#1e2a4a] px-3 py-3 text-center text-[12.5px] text-slate-500">The curve appears after two closed trades in this period.</p>}
    </div>
  );
}

export function PeriodSwitch({ value, onChange, sinceDate }) {
  const opts = [["since", `From ${sinceDate || "today"}`], ["all", "All history"]];
  return (
    <div className="flex gap-1.5">
      {opts.map(([k, l]) => (
        <button key={k} type="button" data-testid={`period-${k}`} onClick={() => onChange(k)}
          className={`rounded border px-3 py-1.5 text-xs transition-colors ${value === k ? "border-amber-500/60 bg-amber-500/10 text-amber-300" : "border-[#1e2a4a] text-slate-400"}`}>{l}</button>
      ))}
    </div>
  );
}

/** What the last 12 hours look like, with the five measurements behind the call. */
export function DayTypeCard({ day, compact }) {
  if (!day) return null;
  const pb = day.playbook || {};
  return (
    <Card title="Day type now" testid="day-type-card"
      sub={`${day.method || ""}${day.updated ? ` Updated ${ist(day.updated)}.` : ""}`}
      right={<span className="flex items-center gap-2"><DayChip type={day.type} /><span className="font-mono text-[11.5px] text-slate-400">{day.score ?? 0} of 5 trend measures</span></span>}>
      {day.type === "NA" ? <p className="text-sm text-slate-500">Waiting for candle data{day.error ? ` (${day.error})` : ""}.</p> : (
        <>
          <div className="grid grid-cols-1 gap-2 text-[12.5px] sm:grid-cols-2">
            <div className="rounded border border-[#1e2a4a] px-3 py-2">A SHORT signal now: <b className="text-slate-100">{PLAYBOOK_NAME[pb.SHORT] || "—"}</b></div>
            <div className="rounded border border-[#1e2a4a] px-3 py-2">A LONG signal now: <b className="text-slate-100">{PLAYBOOK_NAME[pb.LONG] || "—"}</b></div>
          </div>
          {!compact && (
            <div className="mt-3 overflow-x-auto scroll-thin">
              <table className="desk-table">
                <thead><tr><th>Measure (last 12 hours)</th><th>Now</th><th>Trend at</th><th>Range at</th><th>Says trend?</th><th>Reading</th></tr></thead>
                <tbody>
                  {(day.measures || []).map((m) => (
                    <tr key={m.key}>
                      <td>{m.label}</td><td className="font-mono">{m.value}</td><td className="font-mono text-slate-400">≥ {m.trend_at}</td>
                      <td className="font-mono text-slate-400">{m.range_at == null ? "—" : `≤ ${m.range_at}`}</td>
                      <td className={m.vote ? "font-semibold text-teal-300" : "text-slate-400"}>{m.vote ? "Yes" : "No"}</td><td className="text-slate-300">{m.text}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          <p className="mt-2 text-[11.5px] text-slate-500">This describes what price has done. In 11 months of testing it did not predict the next hours and switching by it did not make money. See the Strategy audit.</p>
        </>
      )}
    </Card>
  );
}

/** The 11-month test that sits behind every figure about follow / Plan A / switch. */
export function EvidenceCard({ ev }) {
  if (!ev) return null;
  const rows = [["Follow the signal (1.5R target)", ev.follow], ...(ev.follow_2r ? [["Follow the signal (2R target, from 14 Nov)", ev.follow_2r]] : []), ["Plan A (reverse 1:2)", ev.plan_a], ["Day-type switch", ev.switch]];
  return (
    <Card title="Long test: 11 months of Delta candles" testid="evidence-card"
      sub={`${ev.signals.toLocaleString("en-US")} scalp signals rebuilt with the site's own rules, ${ev.period[0]} to ${ev.period[1]} (${ev.per_day} a day). Same entry rule, 6-hour exit, fees included. 1R = the stop distance (0.5% of price).`}>
      <div className="overflow-x-auto scroll-thin">
        <table className="desk-table">
          <thead><tr><th>Method</th><th>Trades</th><th>Win rate</th><th>Average per trade</th><th>Total</th></tr></thead>
          <tbody>
            {rows.map(([name, b]) => (
              <tr key={name}><td>{name}</td><td className="font-mono">{b.n.toLocaleString("en-US")}</td><td className="font-mono">{b.win}%</td>
                <td className={`font-mono ${pnl(b.mean_r)}`}>{r2(b.mean_r)}</td><td className={`font-mono ${pnl(b.total_r)}`}>{r2(b.total_r)}</td></tr>
            ))}
          </tbody>
        </table>
      </div>
      <ul className="mt-3 list-disc space-y-1 pl-5 text-[12.5px] text-slate-300">
        <li>Fees cost about <b>{n(ev.fee_r, 2)}R</b> on every trade. All three methods lose about that much per trade: the signal has no measurable edge in either direction.</li>
        <li>Day type by hour: range {ev.share.RANGE}%, unclear {ev.share.MIXED}%, trend {(ev.share.TREND_UP || 0) + (ev.share.TREND_DOWN || 0)}%.</li>
        <li>After a trend call, price went further the same way in the next 6 hours {ev.after_trend.continued_pct}% of the time ({ev.after_trend.n} calls). After a range call it stayed inside the range {ev.after_range.stayed_inside_pct}% of the time.</li>
        <li>The call at noon matched the finished day on {ev.noon_vs_eod.same_pct}% of {ev.noon_vs_eod.days} days.</li>
      </ul>
      <div className="mt-3 overflow-x-auto scroll-thin">
        <table className="desk-table">
          <thead><tr><th>Month</th><th>Signals</th><th>Follow</th><th>Plan A</th><th>Switch</th><th>Switch trades</th></tr></thead>
          <tbody>
            {ev.by_month.map((m) => (
              <tr key={m.month}><td className="font-mono">{m.month}</td><td className="font-mono">{m.n}</td><td className={`font-mono ${pnl(m.follow)}`}>{r2(m.follow)}</td>
                <td className={`font-mono ${pnl(m.plan_a)}`}>{r2(m.plan_a)}</td><td className={`font-mono ${pnl(m.switch)}`}>{r2(m.switch)}</td><td className="font-mono">{m.switch_n}</td></tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="mt-2 text-[11px] text-slate-500">Rebuilt on 5-minute candles, so single trades can differ a little from the live 1-minute results. News blocks and daily caps are not rebuilt.</p>
    </Card>
  );
}
