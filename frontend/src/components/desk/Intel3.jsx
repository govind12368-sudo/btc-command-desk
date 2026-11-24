import React from "react";
import { Card, Note, Expand } from "@/components/desk/ui";
import { n, ist, ago } from "@/lib/fmt";

const pc = (v, d = 0) => (v == null ? "—" : `${(v * 100).toFixed(d)}%`);
const mv = (v) => (v == null ? "—" : `${v > 0 ? "+" : v < 0 ? "−" : ""}${Math.abs(v * 100).toFixed(1)}%`);
const usd = (v) => (v == null ? "—" : v >= 1e9 ? `$${(v / 1e9).toFixed(2)}B` : v >= 1e6 ? `$${(v / 1e6).toFixed(0)}M` : `$${n(v / 1e3, 0)}K`);
const NAME = { LOW: "Low", MODERATE: "Moderate", ELEVATED: "Elevated", HIGH: "High", EXTREME: "Extreme" };
// Crash risk reads bad-when-high, recovery reads good-when-high. The band name is always written out, so colour is never the only cue.
const BAND_TEXT = {
  crash: { LOW: "text-teal-300", MODERATE: "text-slate-200", ELEVATED: "text-amber-300", HIGH: "text-orange-300", EXTREME: "text-red-300" },
  recovery: { LOW: "text-slate-300", MODERATE: "text-slate-200", ELEVATED: "text-amber-300", HIGH: "text-teal-300", EXTREME: "text-teal-200" },
};
const Wait = ({ what }) => <p className="rounded border border-line px-3 py-3 text-[12.5px] text-slate-400">{what}: waiting for the history study to finish (a few minutes after a restart). Nothing is estimated in its place.</p>;
const Src = ({ children }) => <Note tone="plain" className="mt-1">{children}</Note>;

function Scale({ score, bands }) {
  return (
    <div className="mt-2">
      <div className="relative h-2">
        <div className="flex h-2 gap-[2px]">{bands.map((b) => <div key={b.name} className="flex-1 rounded-sm bg-slate-600/60" />)}</div>
        <div className="absolute -top-1 h-4 w-[3px] rounded bg-slate-50" style={{ left: `calc(${Math.min(Math.max(score, 0), 100)}% - 1px)` }} />
      </div>
      <div className="mt-1 flex text-[10.5px] text-slate-500">{bands.map((b, i) => <div key={b.name} className="flex-1 text-center">{NAME[b.name]} {i === 0 ? 0 : bands[i - 1].to + 1}–{b.to}</div>)}</div>
    </div>
  );
}

function Score({ kind, title, s, bands, extra }) {
  if (!s) return <div className="rounded-lg border border-line bg-panel2/60 p-4"><div className="text-[13px] font-semibold text-slate-100">{title}</div><p className="mt-2 text-[12.5px] text-amber-300">No data: none of the evidence groups could be read.</p></div>;
  const word = kind === "crash" ? "fall" : "rise";
  const hr = s.history_rate;
  const rate = (x) => (x && x.days ? `${pc(x.events / x.days)} of ${x.days.toLocaleString("en-US")} days` : "no days");
  return (
    <div className="rounded-lg border border-line bg-panel2/60 p-4" data-testid={`intel-${kind}-score`}>
      <div className="text-[13px] font-semibold text-slate-100">{title}</div>
      <div className="mt-1 flex flex-wrap items-baseline gap-x-3">
        <span className="font-mono text-4xl font-bold text-slate-50">{s.score}</span><span className="text-sm text-slate-500">/ 100</span>
        <span className={`text-lg font-bold ${BAND_TEXT[kind][s.band]}`}>{NAME[s.band]}</span>
      </div>
      <Scale score={s.score} bands={bands} />
      <dl className="mt-3 space-y-1 text-[12.5px] text-slate-300">
        <div><dt className="inline text-slate-500">Before: </dt><dd className="inline">{s.previous?.score == null ? "no earlier score yet" : <>{s.previous.score} {s.previous.label}{s.change != null && <> · change <span className="font-mono">{s.change > 0 ? "+" : s.change < 0 ? "−" : ""}{Math.abs(s.change)}</span></>}</>}</dd></div>
        <div><dt className="inline text-slate-500">Evidence: </dt><dd className="inline">{s.confirming} of {s.groups_with_data} groups agree · {s.coverage_pct}% of the evidence has data{s.capped ? ` · raw total ${s.raw}, held at ${s.score} because too few groups agree` : ""}</dd></div>
        <div><dt className="inline text-slate-500">Confidence: </dt><dd className="inline"><b className={s.confidence === "LOW" ? "text-slate-300" : s.confidence === "MEDIUM" ? "text-amber-300" : "text-teal-300"}>{NAME[s.confidence] || "Medium"}</b>. {s.confidence_why}</dd></div>
        {extra}
      </dl>
      <h4 className="mb-1 mt-3 text-[12.5px] font-semibold text-slate-100">Main reasons</h4>
      {s.reasons.length ? <ul className="space-y-0.5 text-[12.5px] text-slate-300">{s.reasons.slice(0, 6).map((r) => <li key={r.group + r.text}><span className="font-mono text-slate-500">+{r.points}</span> {r.text} <span className="text-slate-500">({r.group}{r.tested ? "" : ", not backtested"})</span></li>)}</ul>
        : <p className="text-[12.5px] text-slate-500">None of the {kind === "crash" ? "warning" : "recovery"} signs is active.</p>}
      {hr && <p className="mt-3 text-[12px] text-slate-400">History: on days when the backtested part of this score ({s.tested_score}) was in the <b className="text-slate-200">{NAME[hr.band]}</b> band, a 10% {word} within 7 days followed on {rate(hr.train)} in training, {rate(hr.validation)} in validation and {rate(hr.test)} in the test period.</p>}
      <Expand label="All supporting indicators" testid={`intel-${kind}-indicators`}>
        <div className="overflow-x-auto scroll-thin"><table className="desk-table">
          <thead><tr><th>Group</th><th>Status</th><th className="num">Points</th><th>Indicators</th></tr></thead>
          <tbody>{s.groups.map((g) => (
            <tr key={g.key}><td className="whitespace-nowrap font-semibold">{g.label}{!g.tested && <div className="text-[10.5px] font-normal text-slate-500">live only, not backtested</div>}</td>
              <td className={`whitespace-nowrap ${g.status === "CONFIRMING" ? "text-amber-300" : "text-slate-400"}`}>{g.status === "CONFIRMING" ? "● Agrees" : g.status === "QUIET" ? "○ Quiet" : "— No data"}</td>
              <td className="num font-mono">{g.points} / {g.weight}</td>
              <td><ul className="space-y-0.5">{g.items.map((x) => <li key={x.name} className={x.on ? "text-slate-100" : "text-slate-500"}>{x.on == null ? "—" : x.on ? "✓" : "✗"} <b>{x.name}</b> ({x.max}): {x.text}</li>)}</ul></td></tr>))}</tbody>
        </table></div>
      </Expand>
    </div>
  );
}

export function RiskScores({ r }) {
  if (!r) return <Card title="Crash risk and recovery scores" testid="intel-risk"><Wait what="Risk scores" /></Card>;
  return (
    <Card title="Crash risk and recovery scores" testid="intel-risk" sub={`From 8 independent groups of evidence · BTC ${n(r.price, 0)} · updated ${ago(r.updated)}${r.day_complete ? "" : " · uses today's unfinished daily candle"}`}>
      <div className="grid gap-3 lg:grid-cols-2">
        <Score kind="crash" title="Crash risk (10% fall within 7 days)" s={r.crash} bands={r.bands} />
        <Score kind="recovery" title="Recovery score (10% rise within 7 days)" s={r.recovery} bands={r.bands}
          extra={r.recovery?.stage && <div><dt className="inline text-slate-500">Rally or reversal: </dt><dd className={`inline ${r.recovery.stage.key === "CONFIRMED" ? "text-teal-300" : r.recovery.stage.key === "RELIEF" ? "text-amber-300" : "text-slate-300"}`}>{r.recovery.stage.text}</dd></div>} />
      </div>
      {(r.crash?.verdict || r.recovery?.verdict) && <div className="mt-3"><Note tone="amber"><b>How far to trust these scores.</b> Crash risk: {r.crash?.verdict || "—"} Recovery: {r.recovery?.verdict || "—"}</Note></div>}
      <Src>{r.rule} A falling price alone cannot give a high score. Groups without data are left out, never guessed. Decision support only.</Src>
    </Card>
  );
}

export function LiqMap({ m }) {
  if (!m) return <Card title="Estimated liquidation zones (model)" testid="intel-liq-est"><p className="rounded border border-line px-3 py-3 text-[12.5px] text-slate-400">Estimated liquidation zones: waiting for the first update. Nothing is shown in their place.</p></Card>;
  const rows = [...m.bins].reverse();
  const max = Math.max(1, ...m.bins.map((b) => Math.max(b.long_usd, b.short_usd)));
  const lean = { LONGS: "More estimated long liquidations sit close below price than short liquidations above it: a fall would hit more forced selling (long squeeze risk is the larger one).", SHORTS: "More estimated short liquidations sit close above price than long liquidations below it: a rise would hit more forced buying (short squeeze risk is the larger one).", BALANCED: "Estimated liquidations within 5% are about equal on both sides." }[m.lean];
  const zone = (c) => `${n(c.price_from, 0)}–${n(c.price_to, 0)} (${usd(c.usd)})`;
  return (
    <Card title="Estimated liquidation zones (model)" testid="intel-liq-est" sub={`Where leveraged positions would be forced to close if price got there · BTC ${n(m.price, 0)} · updated ${ago(m.updated)}`}>
      <Note tone="amber"><b>Estimated, not reported.</b> {m.note}</Note>
      <div className="mt-3 overflow-x-auto scroll-thin">
        <table className="desk-table">
          <thead><tr><th>If price moves</th><th className="num">Longs forced out (price falls)</th><th className="num">Shorts forced out (price rises)</th></tr></thead>
          <tbody>{m.within.map((w) => <tr key={w.pct}><td>{w.pct}% either way</td><td className="num font-mono text-red-300">{usd(w.long_usd)}</td><td className="num font-mono text-teal-300">{usd(w.short_usd)}</td></tr>)}</tbody>
        </table>
      </div>
      <p className="mt-2 text-[12.5px] text-slate-300">{lean}</p>
      <div className="mt-3" role="img" aria-label="Estimated liquidation size by price level, from 15 percent above the price to 15 percent below">
        {rows.map((b) => {
          const v = b.from_pct >= 0 ? b.short_usd : b.long_usd;
          return (
            <React.Fragment key={b.from_pct}>
              <div className="flex items-center gap-2" title={`${n(b.price_from, 0)} to ${n(b.price_to, 0)} (${b.from_pct > 0 ? "+" : ""}${b.from_pct}% to ${b.to_pct > 0 ? "+" : ""}${b.to_pct}%): ${usd(v)} of ${b.from_pct >= 0 ? "shorts" : "longs"}`}>
                <div className="w-[104px] shrink-0 text-right font-mono text-[10.5px] text-slate-500">{n(b.from_pct >= 0 ? b.price_to : b.price_from, 0)} <span className="text-slate-600">{b.from_pct >= 0 ? `+${b.to_pct}` : b.from_pct}%</span></div>
                <div className="h-[11px] flex-1"><div className={`h-full rounded-r ${b.from_pct >= 0 ? "bg-teal-400" : "bg-red-400"}`} style={{ width: `${(v / max) * 100}%`, minWidth: v > 0 ? 2 : 0 }} /></div>
                <div className="w-[52px] shrink-0 font-mono text-[10.5px] text-slate-400">{v > 0 ? usd(v) : ""}</div>
              </div>
              {b.from_pct === 0 && <div className="my-1 flex items-center gap-2"><div className="w-[104px] shrink-0 text-right font-mono text-[11px] font-bold text-slate-50">{n(m.price, 0)}</div><div className="h-px flex-1 bg-slate-400" /><div className="w-[52px] shrink-0 text-[10.5px] text-slate-300">price</div></div>}
              {b.from_pct !== 0 && <div className="h-[2px]" />}
            </React.Fragment>
          );
        })}
      </div>
      <Note tone="plain"><span className="text-teal-300">■</span> shorts forced out above price · <span className="text-red-300">■</span> longs forced out below price · each row is 1% of price · longest bar {usd(max)} · hover a row for details</Note>
      <p className="mt-2 text-[12.5px] text-slate-300">Largest estimated zones below price: {m.clusters.long.length ? m.clusters.long.map(zone).join(", ") : "none"}. Above price: {m.clusters.short.length ? m.clusters.short.map(zone).join(", ") : "none"}.</p>
      <Expand label="What the model assumes" testid="intel-liq-est-assumptions"><ul className="list-disc space-y-0.5 pl-4">{m.assumptions.map((a) => <li key={a}>{a}</li>)}<li>A zone is removed once price has traded through it. When open interest falls, all remaining positions are reduced by the same share.</li></ul></Expand>
      <Src>Source: {m.source}{m.from ? ` · positions since ${ist(m.from)}` : ""}. Actual liquidations reported by exchanges are in the card above.</Src>
    </Card>
  );
}

function KindReport({ kind, title, k, main }) {
  const word = kind === "crash" ? "fall" : "rise";
  const h = k[String(main)];
  const parts = [["train", "Training"], ["validation", "Validation"], ["test", "Test (unseen)"]];
  return (
    <div className="mt-4">
      <h4 className="text-[13.5px] font-semibold text-slate-100">{title}</h4>
      <p className="mt-1 text-[12.5px] text-slate-300">{k.verdict}</p>
      <div className="mt-2 overflow-x-auto scroll-thin">
        <table className="desk-table">
          <thead><tr><th>Period</th><th>Warning level</th><th className="num">Days</th><th className="num">Days a 10% {word} followed</th><th className="num">Warning days (separate periods)</th><th className="num">Warnings that were right</th><th className="num">Moves caught</th><th className="num">Moves missed</th><th className="num">False alarms</th><th className="num">Accuracy</th></tr></thead>
          <tbody>{parts.flatMap(([p, label]) => [["warn41", "Score 41+"], ["warn61", "Score 61+"]].map(([wk, wl]) => {
            const s = h[p], w = s[wk];
            return <tr key={p + wk} className={p === "test" ? "font-semibold" : ""}><td className="whitespace-nowrap">{wk === "warn41" ? label : ""}</td><td className="whitespace-nowrap text-slate-400">{wl}</td><td className="num font-mono">{wk === "warn41" ? s.days.toLocaleString("en-US") : ""}</td>
              <td className="num font-mono">{wk === "warn41" ? `${s.events} (${pc(s.base_rate)})` : ""}</td><td className="num font-mono">{w.warnings} ({w.episodes})</td><td className="num font-mono">{pc(w.precision)}</td><td className="num font-mono">{pc(w.recall)}</td>
              <td className="num font-mono">{pc(w.fnr)}</td><td className="num font-mono">{pc(w.fpr)}</td><td className="num font-mono">{pc(w.accuracy)}</td></tr>;
          }))}</tbody>
        </table>
      </div>
      <div className="mt-2 grid gap-3 lg:grid-cols-2">
        <div className="overflow-x-auto scroll-thin">
          <table className="desk-table">
            <thead><tr><th>Score band</th>{parts.map(([p, l]) => <th key={p} className="num">{l}: 10% {word} followed</th>)}</tr></thead>
            <tbody>{h.test.bands.map((b, i) => <tr key={b.band}><td>{NAME[b.band]}</td>{parts.map(([p]) => { const x = h[p].bands[i]; return <td key={p} className="num font-mono">{x.days ? `${pc(x.events / x.days)} of ${x.days}` : "no days"}</td>; })}</tr>)}</tbody>
          </table>
        </div>
        <div className="overflow-x-auto scroll-thin">
          <table className="desk-table">
            <thead><tr><th>Test period, by time frame</th><th className="num">Normal rate</th><th className="num">After a 41+ warning</th><th className="num">Moves caught</th><th className="num">Average {kind === "crash" ? "worst fall" : "best rise"}: all days / after warning</th><th className="num">{kind === "crash" ? "Largest fall" : "Largest rise"}</th></tr></thead>
            <tbody>{Object.keys(k).filter((x) => x !== "verdict").map((hz) => { const t = k[hz].test; return <tr key={hz}><td>Within {hz} days</td><td className="num font-mono">{pc(t.base_rate)}</td><td className="num font-mono">{pc(t.warn41.precision)}</td><td className="num font-mono">{pc(t.warn41.recall)}</td><td className="num font-mono">{mv(t.avg_move_all)} / {mv(t.avg_move_after_warning)}</td><td className="num font-mono">{mv(t.worst_move)}</td></tr>; })}</tbody>
          </table>
        </div>
      </div>
      <Note tone="plain">Prediction error in the test period (Brier score, lower is better): {h.test.brier == null ? "—" : h.test.brier.toFixed(3)} for the score against {h.test.brier_base == null ? "—" : h.test.brier_base.toFixed(3)} for always quoting the normal rate.</Note>
    </div>
  );
}

export function Backtest({ b }) {
  if (!b) return <Card title="Backtest: how the scores did in the past" testid="intel-backtest"><Wait what="Backtest report" /></Card>;
  const s = b.splits;
  return (
    <Card title="Backtest: how the scores did in the past" testid="intel-backtest" sub={`${b.scored_days.toLocaleString("en-US")} past days scored with today's rules · run ${ago(b.generated)}`}>
      <Note tone="plain">Every past day was scored using only what was known at that day's close, then compared with what Bitcoin did next. The days are split by time: training {s.train.from} to {s.train.to}, validation {s.validation.from} to {s.validation.to}, test {s.test.from} to {s.test.to}. The test period is the one that counts: nothing was fitted on it.</Note>
      <KindReport kind="crash" title={`Crash risk: did a ${b.move_pct}% fall follow within ${b.main_horizon} days?`} k={b.crash} main={b.main_horizon} />
      <KindReport kind="recovery" title={`Recovery score: did a ${b.move_pct}% rise follow within ${b.main_horizon} days?`} k={b.recovery} main={b.main_horizon} />
      <Expand label="How the test avoids cheating, and how to read the columns" testid="intel-backtest-method">
        <ul className="list-disc space-y-1 pl-4">
          <li>The rules and weights were written before the test was run and are the same for every day. Nothing was tuned on the validation or test periods.</li>
          <li>The only numbers learned from data are the "top fifth / bottom fifth" cut-offs, taken from the training period alone.</li>
          <li>Publication delays are applied: ETF flows and on-chain data are used from the next day, daily yields one day late, the dollar index a week late, Fed liquidity two days late and monthly inflation about six weeks after the month starts.</li>
          <li>{b.embargo_days} days are left out between the periods so that an outcome can never straddle two of them.</li>
          <li>The live leverage and liquidation group has no long history, so it is not in this test. Whale data needs a paid source and is not in the score at all.</li>
          <li>Warnings that were right = of all warning days, the share followed by the move (precision). Moves caught = of all days before a move, the share that had a warning (recall). Moves missed = the rest (false negatives). False alarms = share of quiet days that had a warning (false positive rate).</li>
          <li>Neighbouring days share the same outcome window, so "separate periods" (warning days no more than 3 days apart counted once) is the fairer count of evidence.</li>
          {b.missing?.length > 0 && <li className="text-amber-300">Not available when this was run, so left out: {b.missing.join(", ")}.</li>}
        </ul>
      </Expand>
      <Src>Data: Binance daily candles and funding, Alternative.me, Farside, FRED, Coin Metrics community, DefiLlama. Past results do not promise future results.</Src>
    </Card>
  );
}
