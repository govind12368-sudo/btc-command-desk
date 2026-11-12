import React from "react";
import { Link } from "react-router-dom";
import { useDesk } from "@/context/DeskContext";
import { useFetch } from "@/hooks/useFetch";
import { Card, Chip, KV, Stat, PageHead, Empty, Note } from "@/components/desk/ui";
import { StrategyCard, CompareTable } from "@/components/desk/Strategy";
import { PillarCard, DirectionPanel } from "@/components/desk/Pillars";
import { CandleChart } from "@/components/desk/CandleChart";
import { n, usd, pct, ist, ago, sgn } from "@/lib/fmt";
import { Sparkles } from "lucide-react";
import { Ring, Sparkline, PillarBars, MtfStrip, VIZ } from "@/components/desk/charts";
import { PILLAR_NAMES } from "@/components/desk/Pillars";
import { NextSetup } from "@/components/desk/NextSetup";

const TONE = { BULLISH: "text-teal-300 border-teal-500/40 bg-teal-500/10", "LEAN BULLISH": "text-teal-300 border-teal-500/30 bg-teal-500/5",
  BEARISH: "text-red-300 border-red-500/40 bg-red-500/10", "LEAN BEARISH": "text-red-300 border-red-500/30 bg-red-500/5", NEUTRAL: "text-slate-300 border-slate-500/40 bg-slate-500/10" };

function DeskBrief({ b }) {
  if (!b) return null;
  return (
    <section data-testid="desk-brief" className="desk-card fade-up relative overflow-hidden p-4 sm:p-5">
      <div className="pointer-events-none absolute -right-16 -top-16 h-48 w-48 rounded-full bg-amber-500/10 blur-3xl" />
      <div className="flex flex-wrap items-center gap-2">
        <Sparkles size={16} className="text-amber-300" />
        <h3 className="text-[15px] font-semibold tracking-tight text-slate-100">Desk Brief</h3>
        <span className={`rounded-full border px-2 py-0.5 font-mono text-[10.5px] font-semibold ${TONE[b.tone] || TONE.NEUTRAL}`} data-testid="brief-tone">{b.tone}</span>
        <span className="font-mono text-[10.5px] text-slate-500">readiness {b.readiness}</span>
      </div>
      <p className="mt-2 text-base font-medium leading-snug text-slate-100 sm:text-lg" data-testid="brief-headline">{b.headline}</p>
      <div className="mt-3 grid gap-4 md:grid-cols-2">
        <div>
          <div className="mb-1 font-mono text-[10.5px] uppercase tracking-wider text-slate-500">What the models see</div>
          <ul className="space-y-1">{(b.points || []).map((p, i) => <li key={i} className="flex gap-2 text-[13px] text-slate-300"><span className="mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full bg-amber-400" />{p}</li>)}</ul>
          {!b.points?.length && <p className="text-[13px] text-slate-500">Waiting for enough live data.</p>}
        </div>
        <div>
          <div className="mb-1 font-mono text-[10.5px] uppercase tracking-wider text-slate-500">Watch list / what is missing</div>
          <ul className="space-y-1">{(b.watch || []).map((p, i) => <li key={i} className="flex gap-2 text-[13px] text-slate-400"><span className="mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full bg-slate-500" />{p}</li>)}</ul>
          {!b.watch?.length && <p className="text-[13px] text-teal-300/80">Nothing outstanding.</p>}
        </div>
      </div>
      <p className="mt-3 text-[10.5px] text-slate-500">{b.note}</p>
    </section>
  );
}


function useDay() {
  const { data } = useFetch("/candles?tf=1h", 60000);
  const bars = (data?.bars || []).slice(-24);
  if (!bars.length) return { bars: null };
  return { bars, open: bars[0].o, high: Math.max(...bars.map((b) => b.h)), low: Math.min(...bars.map((b) => b.l)) };
}

function DecisionHero({ s }) {
  const d = s.decision?.decision;
  const day = useDay();
  const price = s.price;
  const chg = day.open && price ? (price / day.open - 1) * 100 : null;
  const tone = d === "BUY" ? "text-teal-300" : d === "SELL" ? "text-red-300" : d === "MONITOR" ? "text-indigo-300" : "text-amber-300";
  const edge = d === "BUY" ? "before:bg-teal-400" : d === "SELL" ? "before:bg-red-400" : d === "MONITOR" ? "before:bg-indigo-400" : "before:bg-amber-400";
  const total = (ev) => ev.total_checks ?? ev.checks?.length ?? 11;
  return (
    <section data-testid="decision-panel" className={`desk-card relative overflow-hidden before:absolute before:inset-y-0 before:left-0 before:w-1 ${edge}`}>
      <div className="grid gap-0 lg:grid-cols-[1.15fr_1fr_1.05fr]">
        <div className="p-5 lg:border-r lg:border-[#1e2a4a]">
          <div className="text-[13px] font-medium text-slate-400">Trade decision now</div>
          <div data-testid="decision-value" className={`mt-1 font-mono text-5xl font-extrabold tracking-tight sm:text-6xl ${tone}`}>{d || "—"}</div>
          <p className="mt-2 text-[14px] leading-snug text-slate-200" data-testid="decision-reason">{s.decision?.reason}</p>
          <p className="mt-1 text-[11.5px] text-slate-500">{s.decision?.note}</p>
          {s.limits && (
            <div className="mt-3 flex flex-wrap gap-x-5 gap-y-1 text-[12.5px]">
              <span className="text-slate-400">Entries today <b className="font-mono text-slate-100">{s.limits?.entries_today ?? "—"}</b></span>
              <span className="text-slate-400">Realized net today <b className="font-mono text-slate-100">{usd(s.limits?.realized_net_today)}</b></span>
            </div>
          )}
        </div>
        <div className="border-t border-[#1e2a4a] p-5 lg:border-r lg:border-t-0">
          <div className="flex items-baseline justify-between gap-2">
            <div className="text-[13px] font-medium text-slate-400">BTC price · Delta BTCUSD</div>
            {chg != null && <span data-testid="stat-change" className={`rounded px-1.5 py-0.5 font-mono text-[12px] font-semibold ${chg >= 0 ? "bg-teal-500/10 text-teal-300" : "bg-red-500/10 text-red-300"}`}>{chg >= 0 ? "▲" : "▼"} {sgn(chg, 2)}% 24h</span>}
          </div>
          <div data-testid="stat-price" className="mt-1 font-mono text-4xl font-bold tracking-tight text-slate-50">{n(price, 1)}</div>
          <div className="mt-0.5 font-mono text-[11.5px] text-slate-400">bid {n(s.quote?.bid, 1)} · ask {n(s.quote?.ask, 1)}</div>
          <div className="mt-2"><Sparkline bars={day.bars} height={58} testid="price-24h" /></div>
          {day.bars && <div className="mt-1 flex justify-between font-mono text-[11px] text-slate-500"><span>24h low {n(Math.min(day.low, price || day.low), 0)}</span><span>24h high {n(Math.max(day.high, price || day.high), 0)}</span></div>}
        </div>
        <div className="grid grid-cols-2 gap-4 border-t border-[#1e2a4a] p-5 lg:border-t-0">
          <div data-testid="readiness-score" className="col-span-2"><Ring value={s.readiness?.score ?? 0} max={1} size={68} color={VIZ.brand} label="Checklist progress" sub="share of safety checks complete — not a win chance" /></div>
          <Ring testid="stat-scalp" value={s.scalp.passed} max={total(s.scalp)} size={54} stroke={5} color={s.scalp.status === "BUY" ? VIZ.up : s.scalp.status === "SELL" ? VIZ.down : VIZ.s1} label="Scalping" sub={`${s.scalp.passed}/${total(s.scalp)} · ${String(s.scalp.status).toLowerCase()}`} />
          <Ring testid="stat-swing" value={s.swing.passed} max={total(s.swing)} size={54} stroke={5} color={s.swing.status === "BUY" ? VIZ.up : s.swing.status === "SELL" ? VIZ.down : VIZ.s1} label="Swing" sub={`${s.swing.passed}/${total(s.swing)} · ${String(s.swing.status).toLowerCase()}`} />
          <Ring testid="stat-coverage" value={s.live_direction.coverage} max={5} size={54} stroke={5} color={VIZ.s3} label="Pillars" sub={s.live_direction.partial ? "partial coverage" : "complete"} />
          <div data-testid="stat-six" className="flex flex-col justify-center">
            <div className="text-[13px] font-semibold text-slate-100">Six-factor</div>
            <div className={`font-mono text-lg font-bold ${!s.six_factor.complete ? "text-slate-500" : s.six_factor.score > 0 ? "text-teal-300" : s.six_factor.score < 0 ? "text-red-300" : "text-slate-200"}`}>{s.six_factor.complete ? sgn(s.six_factor.score) : "n/a"}</div>
            <div className="text-[11.5px] text-slate-400">{s.six_factor.complete ? s.six_factor.direction.toLowerCase() : "inputs missing"}</div>
          </div>
        </div>
      </div>
      <p className="border-t border-[#1e2a4a] px-5 py-2 text-[11px] text-slate-500">{s.readiness?.note}</p>
    </section>
  );
}

function Flow({ f }) {
  if (!f) return null;
  return (
    <Card title="Buy / Sell Observation" sub={`${f.venue || "Delta"} · ${f.instrument || "BTCUSD"} · taker-side trades, last ${Math.round((f.window_sec || 300) / 60)} min`} testid="flow-panel"
      right={<Chip status={f.agreement === "CONFLICT" ? "CONFLICT" : f.agreement} label={f.agreement === "CONFLICT" ? "FLOW/BOOK CONFLICT" : `FLOW/BOOK ${f.agreement}`} testid="flow-agreement" />}>
      <div data-testid="flow-label" className={`font-mono text-lg font-bold ${f.imbalance > 0 ? "text-teal-400" : f.imbalance < 0 ? "text-red-400" : "text-slate-300"}`}>{f.label}</div>
      <div className="mt-2 h-2 flex rounded overflow-hidden"><div className="bg-teal-500" style={{ width: `${(f.buy_usd / (f.buy_usd + f.sell_usd || 1)) * 100}%` }} /><div className="bg-red-500 flex-1" /></div>
      <div className="mt-3 grid grid-cols-2 gap-x-6">
        <KV k="Buyer-initiated" v={usd(f.buy_usd, 0)} /><KV k="Seller-initiated" v={usd(f.sell_usd, 0)} />
        <KV k="Trade imbalance" v={sgn(f.imbalance, 3)} testid="flow-imbalance" /><KV k="Trades" v={f.trades} />
        <KV k="Book bid share" v={pct(f.bid_share, 1)} /><KV k="Book ask share" v={pct(f.ask_share, 1)} />
      </div>
      <p className="mt-2 font-mono text-[10.5px] text-slate-500">window {ist(f.window_start)} → {ist(f.window_end)} · book {ist(f.book_time)} · strong pressure at |imbalance| ≥ 0.65</p>
    </Card>
  );
}

function JournalSummary() {
  const sig = useFetch("/signals?size=5", 30000);
  const val = useFetch("/validation?size=3", 60000);
  return (
    <Card title="Automatic Journal & Validation" testid="journal-summary" right={<Link to="/journal" className="text-xs text-amber-400" data-testid="open-journal">Journal →</Link>}>
      <div className="font-mono text-[11px] text-slate-500 mb-1">Model signals captured: {sig.data?.total ?? "—"}</div>
      {sig.data?.items?.length ? sig.data.items.map((x) => (
        <div key={x.id} className="flex items-center justify-between py-1 text-xs border-b border-[#1e2a4a]/50"><span className="font-mono">{x.strategy} {x.direction} @ {n(x.entry, 1)}</span><Chip status={x.observation} /></div>
      )) : <Empty>No model signal has passed every active check yet.</Empty>}
      <div className="mt-3 font-mono text-[11px] text-slate-500">Direction snapshots: {val.data?.total ?? "—"} · 30d history {val.data?.history?.bars ?? "—"} bars, {val.data?.history?.gap_count ?? "—"} gaps</div>
      {val.data?.items?.map((x) => (
        <div key={x.id} className="flex items-center justify-between py-1 text-xs border-b border-[#1e2a4a]/50"><span className="font-mono">{x.model} {x.direction} {ist(x.capture_time)}</span><Chip status={x.outcome} /></div>
      ))}
      <Link to="/validation" className="mt-2 inline-block text-xs text-amber-400" data-testid="open-validation">Validation →</Link>
    </Card>
  );
}

function NewsRadar() {
  const { data } = useFetch("/news?size=8", 30000);
  return (
    <Card title="Global News Radar" sub="Information only · news and event reviews do not affect signals or BTC Direction" testid="news-radar" right={<Link to="/news" className="text-xs text-amber-400" data-testid="open-news">All news →</Link>}>
      {!data ? <Empty>Loading…</Empty> : (data.items || []).map((x) => (
        <a key={x.id} href={x.link} target="_blank" rel="noreferrer" className="block py-2 border-b border-[#1e2a4a]/50 hover:bg-[#1a264a]/40 transition-colors">
          <div className="flex items-center gap-2 flex-wrap"><Chip status={x.impact} /><span className="font-mono text-[10.5px] text-slate-500">{x.publisher} · {x.published_at ? ist(x.published_at) : x.time_error}</span><span className="font-mono text-[10px] text-slate-500">{x.keyword_direction}</span></div>
          <div className="mt-1 text-[13px] text-slate-200">{x.title}</div>
        </a>
      ))}
    </Card>
  );
}

function SourceHealth({ sources, recovery }) {
  const { isAdmin } = useDesk();
  return (
    <Card title="Execution controls & source health" testid="source-health" right={isAdmin && <Link to="/audit" className="text-xs text-amber-400" data-testid="open-audit">Audit →</Link>}>
      <div className="flex flex-wrap gap-2 mb-3">
        <Link to="/journal" data-testid="ctl-fill" className="rounded border border-[#1e2a4a] px-3 py-1.5 text-xs hover:border-amber-500/40">Record actual fill</Link>
        <Link to="/planner" data-testid="ctl-planner" className="rounded border border-[#1e2a4a] px-3 py-1.5 text-xs hover:border-amber-500/40">Trade planner</Link>
      </div>
      <p className="mb-2 text-[11px] text-slate-500">This desk never places exchange orders. Execute manually on Delta, then record the actual fill.</p>
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-x-4">
        {(sources || []).map((s) => (
          <div key={s.key} className="min-w-0 flex items-center justify-between gap-2 py-1 border-b border-[#1e2a4a]/40" data-testid={`src-${s.key}`}>
            <span className="min-w-0 flex-1 truncate text-[11.5px] text-slate-300" title={s.error || s.name}>{s.name}</span>
            <span className="flex shrink-0 items-center gap-1.5"><span className="hidden sm:inline font-mono text-[10px] text-slate-500">{ago(s.last_success)}</span><Chip status={s.status} /></span>
          </div>
        ))}
      </div>
      <p className="mt-2 font-mono text-[10.5px] text-slate-500">Recovery: {recovery?.state} · last {recovery?.last_result?.result || "—"} {recovery?.last_result?.family ? `(${recovery.last_result.family})` : ""}</p>
    </Card>
  );
}

function useView() {
  const [v, setV] = React.useState(() => { try { return localStorage.getItem("vantage-view") || "simple"; } catch { return "simple"; } });
  const set = (x) => { setV(x); try { localStorage.setItem("vantage-view", x); } catch { /* storage unavailable */ } };
  return [v, set];
}

export default function Dashboard() {
  const { state: s, isAdmin } = useDesk();
  const [view, setView] = useView();
  if (!s?.scalp) return <Empty>Connecting to live sources… first calculation appears within a few seconds.</Empty>;
  const lv = s.scalp.levels || s.swing.levels;
  return (
    <div className="space-y-4">
      <PageHead title="BTC Dashboard" sub={`Delta BTCUSD perpetual · computed ${ist(s.computed_at)} · quote ${s.quote?.source || "—"} (exchange time ${ist(s.quote?.event_time)})`}
        right={<div className="flex rounded-md border border-[#1e2a4a] p-0.5" role="tablist" aria-label="Dashboard view" data-testid="view-toggle">
          {[["simple", "Simple view"], ["detailed", "Detailed view"]].map(([k, l]) => (
            <button key={k} role="tab" aria-selected={view === k} onClick={() => setView(k)} data-testid={`view-${k}`}
              className={`rounded px-3 py-1.5 text-xs font-semibold transition-colors ${view === k ? "bg-amber-500 text-[#0b132b]" : "text-slate-300 hover:text-slate-50"}`}>{l}</button>
          ))}
        </div>} />
      <DecisionHero s={s} />
      <NextSetup scalp={s.scalp} swing={s.swing} isAdmin={isAdmin} />
      <DeskBrief b={s.brief} />
      <div className="grid gap-4 lg:grid-cols-[1.2fr_1fr]">
        <Card title="BTC Direction · confirmed 15-minute model" sub="Completed-candle confirmation · OKX market data + Fed/EIA macro data · news/calendar excluded" testid="direction-card"><DirectionPanel live={s.live_direction} /></Card>
        <Card title="Five-pillar balance" sub="Each bar = pillar score × its share of the model (same shares as the direction model). Striped = data unavailable." testid="pillar-balance" right={<Link to="/pillars" className="text-xs text-amber-400">Pillar details</Link>}>
          <PillarBars pillars={s.pillars} names={PILLAR_NAMES} live={s.live_direction} />
          <div className="mt-4"><div className="mb-1.5 text-[12px] text-slate-400">Trend by timeframe (Delta completed candles)</div><MtfStrip rows={s.mtf?.rows} /></div>
        </Card>
      </div>
      <Card title="Price chart" sub="Hover a candle for its open, high, low, close and volume" testid="chart-card"><CandleChart levels={lv} /></Card>
      {view === "detailed" ? (<>
      <div className="grid gap-4 lg:grid-cols-2">
        <StrategyCard ev={s.scalp} title="Scalping Trade Signal" to="/scalping" />
        <StrategyCard ev={s.swing} title="Swing Trade Signal" to="/swing" />
      </div>
      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-5">
        {Object.entries(s.pillars).map(([k, p]) => <PillarCard key={k} k={k} p={p} compact />)}
      </div>
      <div className="grid gap-4 lg:grid-cols-3">
        <Flow f={s.flow} />
        <JournalSummary />
        <Card title="Multi-timeframe evidence" sub="Supporting data — Delta completed candles" testid="mtf-card">
          {s.mtf?.rows?.map((r) => (
            <div key={r.tf} className="flex items-center justify-between py-1 border-b border-[#1e2a4a]/40 text-xs"><span className="font-mono text-amber-300 w-10">{r.tf}</span><span className="font-mono text-slate-400 truncate min-w-0 px-2">{r.close ? `${n(r.close, 0)} · ${r.above_vwap ? "above" : "below"} VWAP · vol ${n(r.volume_ratio, 2)}` : ""}</span><Chip status={r.trend} /></div>
          ))}
          <p className="mt-2 text-[11px] text-slate-400">{s.mtf?.summary}</p>
        </Card>
      </div>
      <Card title="Active checks — Scalping vs Swing" sub="Click a check on strategy pages to expand. Every active check must pass; F31 review/journal is excluded." testid="compare-card"><CompareTable scalp={s.scalp} swing={s.swing} /></Card>
      <div className="grid gap-4 xl:grid-cols-[1fr_1fr]">
        <NewsRadar />
        <SourceHealth sources={s.sources} recovery={s.recovery} />
      </div>
      </>) : (
        <p className="text-center text-[12.5px] text-slate-500">Simple view shows what matters for a decision. Switch to <button className="text-amber-300 underline-offset-2 hover:underline" onClick={() => setView("detailed")}>Detailed view</button> for every check, pillar note, order flow, journal, news and source health.</p>
      )}
      <Note>{s.monitoring_note}</Note>
    </div>
  );
}
