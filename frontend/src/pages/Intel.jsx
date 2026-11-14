import React, { useState } from "react";
import { useFetch } from "@/hooks/useFetch";
import { Card, PageHead, Note } from "@/components/desk/ui";
import { n, ist, ago } from "@/lib/fmt";
import IntelChart from "@/components/desk/IntelChart";

const BIAS = { BULLISH: "border-teal-500/40 bg-teal-500/10 text-teal-300", BEARISH: "border-red-500/40 bg-red-500/10 text-red-300", NEUTRAL: "border-slate-500/40 bg-slate-500/10 text-slate-300" };
const SIDE = { bull: "text-teal-300", bear: "text-red-300", neutral: "text-amber-300" };
const big = (v) => (v == null ? "—" : Math.abs(v) >= 1e9 ? `$${(v / 1e9).toFixed(2)}B` : Math.abs(v) >= 1e6 ? `$${(v / 1e6).toFixed(1)}M` : Math.abs(v) >= 1e3 ? `$${(v / 1e3).toFixed(0)}K` : `$${n(v, 0)}`);
const pc = (v, d = 2) => (v == null ? "—" : `${v > 0 ? "+" : v < 0 ? "−" : ""}${Math.abs(v).toFixed(d)}%`);
const tone = (v) => (v == null ? "text-slate-300" : v > 0 ? "text-teal-300" : v < 0 ? "text-red-300" : "text-slate-300");
const vote = (v) => (v > 0 ? ["▲ bullish", "text-teal-300"] : v < 0 ? ["▼ bearish", "text-red-300"] : ["— neutral", "text-slate-400"]);

const Badge = ({ label }) => <span className={`inline-flex whitespace-nowrap rounded border px-2 py-0.5 text-[11px] font-semibold ${BIAS[label] || "border-slate-600 text-slate-400"}`}>{label ? label[0] + label.slice(1).toLowerCase() : "No data"}</span>;
const Src = ({ source, at }) => <p className="mt-2 text-[11px] text-slate-500">Source: {source || "—"}{at ? ` · updated ${ago(at)}` : ""}</p>;
const Missing = ({ what, why }) => <p className="rounded border border-[#1e2a4a] px-3 py-3 text-[12.5px] text-slate-400">{what}: <b className="text-amber-300">unavailable</b>{why ? ` (${why})` : ""}. Nothing is estimated in its place.</p>;
function Fig({ label, value, sub, cls = "text-slate-50" }) {
  return (
    <div className="rounded-lg border border-[#1e2a4a] bg-[#0f172a]/60 px-3 py-2.5">
      <div className="text-[11.5px] text-slate-400">{label}</div>
      <div className={`font-mono text-lg font-bold ${cls}`}>{value}</div>
      {sub && <div className="text-[11px] text-slate-500">{sub}</div>}
    </div>
  );
}

function Technical({ d }) {
  const [tf, setTf] = useState("1h");
  const { data: ch } = useFetch(`/intel/chart?tf=${tf}`, tf === "1m" || tf === "5m" ? 20000 : 60000);
  const a = d.timeframes?.[tf];
  const ok = a?.status === "OK";
  return (
    <Card title="Technical analysis" sub="Each timeframe is analysed on its own, on completed candles. A bias needs at least 3 more signals one way than the other." testid="intel-technical">
      <div className="overflow-x-auto scroll-thin">
        <table className="desk-table">
          <thead><tr><th>Timeframe</th><th>Bias</th><th>Structure</th><th className="num">RSI</th><th className="num">ADX</th><th className="num">ATR</th><th>MACD</th><th>vs EMA200</th><th>Latest event</th></tr></thead>
          <tbody>{d.order.map((k) => {
            const x = d.timeframes?.[k];
            if (!x || x.status !== "OK") return <tr key={k}><td className="font-mono">{k}</td><td colSpan={8} className="text-slate-500">{x ? `Insufficient data (${x.bars} of ${x.needed} candles)` : "Loading…"}</td></tr>;
            const e = x.events?.[0];
            return (
              <tr key={k} onClick={() => setTf(k)} className={`cursor-pointer ${tf === k ? "bg-amber-500/5" : ""}`}>
                <td className="font-mono font-semibold">{k}</td><td><Badge label={x.bias} /></td><td className="text-[12px]">{x.structure?.label}</td>
                <td className={`num font-mono ${x.rsi >= 70 ? "text-red-300" : x.rsi <= 30 ? "text-teal-300" : ""}`}>{n(x.rsi, 0)}</td><td className="num font-mono">{n(x.adx, 0)}</td><td className="num font-mono">{pc(x.atr_pct).replace("+", "")}</td>
                <td className={`text-[12px] ${tone(x.macd?.hist)}`}>{x.macd?.hist > 0 ? "Rising" : "Falling"}</td><td className={`text-[12px] ${x.ema?.["200"] == null ? "text-slate-500" : x.price > x.ema["200"] ? "text-teal-300" : "text-red-300"}`}>{x.ema?.["200"] == null ? "—" : x.price > x.ema["200"] ? "Above" : "Below"}</td>
                <td className={`text-[12px] ${SIDE[e?.side] || "text-slate-500"}`}>{e?.text || "None"}</td>
              </tr>);
          })}</tbody>
        </table>
      </div>
      <div className="mt-4 flex flex-wrap items-center gap-1.5" role="tablist" aria-label="Chart timeframe">
        {d.order.map((k) => <button key={k} type="button" role="tab" aria-selected={tf === k} data-testid={`intel-tf-${k}`} onClick={() => setTf(k)} className={`rounded border px-3 py-1.5 font-mono text-xs ${tf === k ? "border-amber-500/60 bg-amber-500/10 text-amber-300" : "border-[#1e2a4a] text-slate-400"}`}>{k}</button>)}
        <span className="ml-2 text-[11px] text-slate-500">Chart time is IST. Lines: EMA20 yellow, EMA50 blue, EMA200 purple, S/R dashed.</span>
      </div>
      <div className="mt-2"><IntelChart data={ch} /></div>
      {ok ? (
        <div className="mt-4 grid grid-cols-1 gap-4 lg:grid-cols-3">
          <section>
            <h3 className="mb-1.5 text-[13px] font-semibold text-slate-100">{tf} signals <Badge label={a.bias} /> <span className="font-mono text-[11px] text-slate-400">score {a.score > 0 ? "+" : ""}{a.score}</span></h3>
            <ul className="space-y-1 text-[12.5px]">{a.votes.map((v) => <li key={v.name} className="flex justify-between gap-3"><span className="text-slate-300">{v.name}<span className="block text-[11px] text-slate-500">{v.text}</span></span><span className={`whitespace-nowrap font-semibold ${vote(v.vote)[1]}`}>{vote(v.vote)[0]}</span></li>)}</ul>
            <p className="mt-2 text-[11.5px] text-slate-400">Trend strength: {a.trend_strength} (ADX {n(a.adx, 0)}, +DI {n(a.plus_di, 0)}, −DI {n(a.minus_di, 0)}).</p>
          </section>
          <section>
            <h3 className="mb-1.5 text-[13px] font-semibold text-slate-100">{tf} indicators</h3>
            <dl className="grid grid-cols-2 gap-x-4 gap-y-1 font-mono text-[12px]">
              {[["RSI (14)", n(a.rsi, 1)], ["MACD", `${n(a.macd.line, 1)} / ${n(a.macd.signal, 1)}`], ["EMA 20", n(a.ema["20"], 1)], ["EMA 50", n(a.ema["50"], 1)], ["EMA 100", n(a.ema["100"], 1)], ["EMA 200", n(a.ema["200"], 1)],
                ["SMA 50", n(a.sma["50"], 1)], ["SMA 100", n(a.sma["100"], 1)], ["SMA 200", n(a.sma["200"], 1)], ["VWAP", n(a.vwap, 1)], ["Bollinger", `${n(a.bollinger.lower, 0)} – ${n(a.bollinger.upper, 0)}`], ["ATR (14)", `${n(a.atr, 1)} (${n(a.atr_pct, 2)}%)`],
                ["Volume vs avg", a.volume.ratio == null ? "—" : `${n(a.volume.ratio, 2)}x`], ["OBV 20-bar", a.obv_change_20 > 0 ? "Rising" : "Falling"]].map(([k, v]) => <React.Fragment key={k}><dt className="font-sans text-slate-400">{k}</dt><dd className="text-right text-slate-100">{v}</dd></React.Fragment>)}
            </dl>
          </section>
          <section>
            <h3 className="mb-1.5 text-[13px] font-semibold text-slate-100">{tf} levels</h3>
            <dl className="grid grid-cols-2 gap-x-4 gap-y-1 font-mono text-[12px]">
              {(a.resistance || []).slice(0, 3).reverse().map((z, i, arr) => <React.Fragment key={`r${z.price}`}><dt className="font-sans text-red-300">Resistance {arr.length - i}</dt><dd className="text-right">{n(z.price, 0)} <span className="text-slate-500">({z.touches} touch{z.touches > 1 ? "es" : ""})</span></dd></React.Fragment>)}
              <dt className="font-sans text-slate-400">Price</dt><dd className="text-right font-bold text-slate-50">{n(a.price, 1)}</dd>
              {(a.support || []).slice(0, 3).map((z, i) => <React.Fragment key={`s${z.price}`}><dt className="font-sans text-teal-300">Support {i + 1}</dt><dd className="text-right">{n(z.price, 0)} <span className="text-slate-500">({z.touches} touch{z.touches > 1 ? "es" : ""})</span></dd></React.Fragment>)}
              {ch?.volume_profile && <><dt className="font-sans text-slate-400">Volume point of control</dt><dd className="text-right">{n(ch.volume_profile.poc, 0)}</dd><dt className="font-sans text-slate-400">Value area (70%)</dt><dd className="text-right">{n(ch.volume_profile.value_area_low, 0)} – {n(ch.volume_profile.value_area_high, 0)}</dd></>}
              {a.fib && Object.entries(a.fib.levels).map(([k, v]) => <React.Fragment key={k}><dt className="font-sans text-slate-400">Fib {k}</dt><dd className="text-right">{n(v, 0)}</dd></React.Fragment>)}
            </dl>
            {a.fib && <p className="mt-1 text-[11px] text-slate-500">Fibonacci from the last {a.fib.bars}-bar swing ({n(a.fib.swing_low, 0)} – {n(a.fib.swing_high, 0)}, {a.fib.direction.toLowerCase()}).</p>}
          </section>
        </div>
      ) : <p className="mt-3 text-sm text-slate-500">{a ? "INSUFFICIENT DATA for this timeframe." : "Loading…"}</p>}
      {ok && a.events?.length > 0 && <ul className="mt-3 list-disc space-y-0.5 pl-5 text-[12.5px]">{a.events.map((e) => <li key={e.kind + e.text} className={SIDE[e.side]}>{e.text}</li>)}</ul>}
      <Src source={a?.source} at={a?.updated} />
    </Card>
  );
}

function Pressure({ p }) {
  if (!p) return <Missing what="Spot buy and sell pressure" why="waiting for the first update" />;
  return (
    <>
      <div className="space-y-2">
        {Object.entries(p.windows).map(([k, w]) => {
          const s = w.buy_share == null ? 50 : w.buy_share * 100;
          return (
            <div key={k} className="flex items-center gap-3 text-[12px]">
              <span className="w-14 font-mono text-slate-400">{k}</span>
              <div className="flex h-3 flex-1 overflow-hidden rounded bg-[#0f172a]" role="img" aria-label={`${k}: buyers ${s.toFixed(0)} percent, sellers ${(100 - s).toFixed(0)} percent`}>
                <div className="bg-teal-400" style={{ width: `${s}%` }} /><div className="w-0.5 bg-[#0b1220]" /><div className="flex-1 bg-red-400" />
              </div>
              <span className="w-40 text-right font-mono text-slate-200">{s.toFixed(1)}% buy · {big(w.buy_usd + w.sell_usd)}</span>
              <span className={`w-20 text-right font-semibold ${w.label === "BUYERS" ? "text-teal-300" : w.label === "SELLERS" ? "text-red-300" : "text-slate-400"}`}>{w.label[0] + w.label.slice(1).toLowerCase()}</span>
            </div>);
        })}
      </div>
      <Src source={p.source} at={p.updated} />
    </>
  );
}

function Derivatives({ d }) {
  if (!d) return <Card title="Futures and derivatives" testid="intel-deriv"><Missing what="Derivatives data" why="no exchange has answered yet" /></Card>;
  const p = d.positioning;
  return (
    <Card title="Futures and derivatives" sub={`Perpetual futures on ${d.venues.length} exchanges, added together. Funding is per 8 hours.`} testid="intel-deriv" right={<Badge label={p.label === "INSUFFICIENT DATA" ? null : p.label} />}>
      <div className="grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-6">
        <Fig label="Open interest" value={big(d.oi_usd)} sub={d.change?.["24h"] ? `24h ${pc(d.change["24h"].oi_pct, 1)}` : "24h change: collecting"} />
        <Fig label="OI change 1h / 4h" value={`${d.change?.["1h"] ? pc(d.change["1h"].oi_pct, 1) : "—"} / ${d.change?.["4h"] ? pc(d.change["4h"].oi_pct, 1) : "—"}`} />
        <Fig label="Funding (OI-weighted)" value={d.funding_8h == null ? "—" : `${(d.funding_8h * 100).toFixed(4)}%`} cls={tone(d.funding_8h)} sub={d.funding_8h == null ? "" : `${(d.funding_8h * 3 * 365 * 100).toFixed(1)}% a year`} />
        <Fig label="Accounts long" value={d.long_share == null ? "—" : `${(d.long_share * 100).toFixed(0)}%`} sub={d.long_share == null ? "" : `long/short ${(d.long_share / (1 - d.long_share)).toFixed(2)}`} />
        <Fig label="Perpetual premium" value={pc(d.premium_pct, 3)} cls={tone(d.premium_pct)} sub="futures vs spot index" />
        <Fig label="Futures volume 24h" value={big(d.volume_24h_usd)} />
      </div>
      {d.change_note && <p className="mt-2 text-[11.5px] text-slate-500">{d.change_note}</p>}
      <div className="mt-3 grid grid-cols-1 gap-4 lg:grid-cols-2">
        <section>
          <h3 className="mb-1.5 text-[13px] font-semibold text-slate-100">Positioning: why</h3>
          <ul className="space-y-1 text-[12.5px]">{p.votes.map((v) => <li key={v.name} className="flex justify-between gap-3"><span className="text-slate-300">{v.name}<span className="block text-[11px] text-slate-500">{v.text}</span></span><span className={`whitespace-nowrap font-semibold ${vote(v.vote)[1]}`}>{vote(v.vote)[0]}</span></li>)}</ul>
          {p.flags.length > 0 ? <ul className="mt-2 list-disc space-y-0.5 pl-5 text-[12.5px]">{p.flags.map((f) => <li key={f.kind} className={SIDE[f.side]}>{f.text}</li>)}</ul> : <p className="mt-2 text-[12px] text-slate-500">No leverage warning is active (crowded longs or shorts, fast OI build-up, squeeze set-up).</p>}
        </section>
        <section className="overflow-x-auto scroll-thin">
          <table className="desk-table">
            <thead><tr><th>Exchange</th><th className="num">Open interest</th><th className="num">Funding 8h</th><th className="num">Premium</th><th className="num">Long accounts</th></tr></thead>
            <tbody>
              {d.venues.map((v) => <tr key={v.venue}><td>{v.venue}</td><td className="num font-mono">{big(v.oi_usd)}</td><td className={`num font-mono ${tone(v.funding_8h)}`}>{(v.funding_8h * 100).toFixed(4)}%</td><td className="num font-mono">{pc(v.premium_pct, 3)}</td><td className="num font-mono">{v.long_share == null ? "—" : `${(v.long_share * 100).toFixed(0)}%`}</td></tr>)}
              <tr><td>CME Bitcoin futures basis</td><td colSpan={4} className="text-amber-300">Unavailable: {d.cme_basis.reason}</td></tr>
            </tbody>
          </table>
        </section>
      </div>
      <Src source="Exchange public APIs (listed in the table); the funding shown is each exchange's current predicted rate" at={d.updated} />
    </Card>
  );
}

function Liquidations({ l }) {
  const max = Math.max(1, ...l.hourly.map((b) => Math.max(b.long_usd, b.short_usd)));
  return (
    <Card title="Liquidations (exchange-reported)" sub="Actual forced closures reported by exchanges. Estimated liquidation zones (the heatmap) are a separate, later module." testid="intel-liq">
      {l.cascade && <Note tone="red">{l.cascade.text}</Note>}
      <div className="overflow-x-auto scroll-thin">
        <table className="desk-table">
          <thead><tr><th>Window</th><th className="num">Longs liquidated</th><th className="num">Shorts liquidated</th><th className="num">Events</th><th>Heavier side</th></tr></thead>
          <tbody>{Object.entries(l.windows).map(([k, w]) => <tr key={k}><td className="font-mono">{k}</td><td className="num font-mono text-red-300">{big(w.long_usd)}</td><td className="num font-mono text-teal-300">{big(w.short_usd)}</td><td className="num font-mono">{w.count}</td>
            <td className="text-[12px]">{w.count === 0 ? <span className="text-slate-500">None reported</span> : w.long_usd > w.short_usd * 1.5 ? "Longs (price fell into them)" : w.short_usd > w.long_usd * 1.5 ? "Shorts (price rose into them)" : "Balanced"}</td></tr>)}</tbody>
        </table>
      </div>
      <h3 className="mb-1 mt-4 text-[13px] font-semibold text-slate-100">Last 24 hours, by hour</h3>
      <div className="flex h-24 items-end gap-0.5" role="img" aria-label="Hourly liquidations for the last 24 hours: longs in red, shorts in teal">
        {l.hourly.map((b) => (
          <div key={b.t} className="flex h-full flex-1 items-end gap-px" title={`${ist(b.t)}: longs ${big(b.long_usd)}, shorts ${big(b.short_usd)}`}>
            <div className="flex-1 rounded-t bg-red-400" style={{ height: `${(b.long_usd / max) * 100}%`, minHeight: b.long_usd ? 2 : 0 }} />
            <div className="flex-1 rounded-t bg-teal-400" style={{ height: `${(b.short_usd / max) * 100}%`, minHeight: b.short_usd ? 2 : 0 }} />
          </div>))}
      </div>
      <p className="mt-1 text-[11px] text-slate-500"><span className="text-red-300">■</span> longs liquidated · <span className="text-teal-300">■</span> shorts liquidated · tallest bar {big(max)} · hover a bar for the hour</p>
      <h3 className="mb-1 mt-4 text-[13px] font-semibold text-slate-100">Large events (≥ {big(l.large_threshold_usd)})</h3>
      {l.large.length ? (
        <div className="max-h-56 overflow-auto scroll-thin"><table className="desk-table">
          <thead><tr><th>Time (IST)</th><th>Exchange</th><th>Side liquidated</th><th className="num">Size</th><th className="num">Price</th></tr></thead>
          <tbody>{l.large.map((e) => <tr key={`${e.ex}${e.t}${e.usd}`}><td className="font-mono text-[11.5px]">{ist(e.t)}</td><td>{e.ex}</td><td className={e.side === "long" ? "text-red-300" : "text-teal-300"}>{e.side === "long" ? "Long" : "Short"}</td><td className="num font-mono">{big(e.usd)}</td><td className="num font-mono">{n(e.price, 1)}</td></tr>)}</tbody>
        </table></div>
      ) : <p className="text-[12.5px] text-slate-500">No large liquidation reported in the last 24 hours.</p>}
      <p className="mt-2 text-[11px] text-slate-500">Feeds working: {l.feeds_ok.join(", ") || "none"}{l.feeds_down.length ? ` · not working: ${l.feeds_down.join(", ")}` : ""}{l.collecting_since ? ` · collecting since ${ist(l.collecting_since)}` : ""}. {l.note}</p>
    </Card>
  );
}

function Correlations({ c }) {
  if (!c) return <Card title="Correlations" testid="intel-corr"><Missing what="Correlations" why="waiting for the first update" /></Card>;
  const cell = (v) => <td className={`num font-mono ${v == null ? "text-slate-500" : v >= 0.5 ? "text-teal-300" : v <= -0.5 ? "text-red-300" : "text-slate-200"}`}>{v == null ? "—" : v.toFixed(2)}</td>;
  return (
    <Card title="Correlations with Bitcoin" sub="+1 = moves together, −1 = moves opposite, 0 = no relationship. Daily returns." testid="intel-corr">
      <div className="overflow-x-auto scroll-thin">
        <table className="desk-table">
          <thead><tr><th>Market</th><th className="num">7 days</th><th className="num">30 days</th><th className="num">90 days</th><th>Note</th><th>Source</th></tr></thead>
          <tbody>{c.rows.map((r) => r.status === "OK"
            ? <tr key={r.name}><td>{r.name}</td>{cell(r.corr["7"])}{cell(r.corr["30"])}{cell(r.corr["90"])}<td className="text-[12px] text-amber-300">{r.shift || ""}</td><td className="text-[11.5px] text-slate-400">{r.source} · to {r.last_day}</td></tr>
            : <tr key={r.name}><td>{r.name}</td><td colSpan={5} className="text-[12px] text-amber-300">Unavailable: {r.reason}</td></tr>)}</tbody>
        </table>
      </div>
      <p className="mt-2 text-[11px] text-slate-500">{c.method} BTC: {c.btc_source}. Updated {ago(c.updated)}.</p>
    </Card>
  );
}

export default function Intel() {
  const { data: d } = useFetch("/intel", 10000);
  if (!d) return <div className="text-sm text-slate-400">Loading market intelligence…</div>;
  const o = d.overview, g = o.global, dl = o.delta, lv = o.levels || {};
  const down = d.sources.filter((s) => s.status === "UNAVAILABLE");
  return (
    <div className="space-y-4" data-testid="intel-page">
      <PageHead title="Market Intelligence" sub="Live monitoring of what moves Bitcoin. Decision support only: it places no trades and makes no promises about direction." />
      <Card title="Market overview" testid="intel-overview" sub={`${o.price_source} · ${o.price_time ? ago(o.price_time) : "—"}`}>
        <div className="grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-6">
          <Fig label="BTC / USD" value={n(o.price, 1)} sub={dl ? `24h ${n(dl.low_24h, 0)} – ${n(dl.high_24h, 0)}` : null} />
          <Fig label="24h change" value={pc(o.change_24h_pct)} cls={tone(o.change_24h_pct)} sub={g ? `all exchanges ${pc(g.change_24h_pct)}` : null} />
          <Fig label="Volume 24h (all exchanges)" value={g ? big(g.volume_24h) : "Unavailable"} sub={dl ? `Delta ${big(dl.turnover_24h_usd)}` : null} />
          <Fig label="Market cap" value={g ? big(g.market_cap) : "Unavailable"} sub={g ? g.source : "CoinGecko not answering"} />
          <Fig label="Trend (daily / 4h)" value={<span className="flex gap-1.5"><Badge label={o.trend.daily} /><Badge label={o.trend.h4} /></span>} sub={o.trend.structure_daily} />
          <Fig label="Volatility (ATR)" value={o.volatility.atr_1h_pct == null ? "—" : `${n(o.volatility.atr_1h_pct, 2)}% / h`} sub={o.volatility.atr_1d_pct == null ? null : `${n(o.volatility.atr_1d_pct, 2)}% a day`} />
        </div>
        <div className="mt-2 grid grid-cols-2 gap-2 sm:grid-cols-4">
          <Fig label="Previous day high" value={n(lv.prev_day_high, 1)} cls={o.price > lv.prev_day_high ? "text-teal-300" : "text-slate-50"} sub={o.price > lv.prev_day_high ? "price is above" : null} />
          <Fig label="Previous day low" value={n(lv.prev_day_low, 1)} cls={o.price < lv.prev_day_low ? "text-red-300" : "text-slate-50"} sub={o.price < lv.prev_day_low ? "price is below" : null} />
          <Fig label="Previous week high" value={n(lv.prev_week_high, 1)} cls={o.price > lv.prev_week_high ? "text-teal-300" : "text-slate-50"} sub={o.price > lv.prev_week_high ? "price is above" : null} />
          <Fig label="Previous week low" value={n(lv.prev_week_low, 1)} cls={o.price < lv.prev_week_low ? "text-red-300" : "text-slate-50"} sub={o.price < lv.prev_week_low ? "price is below" : null} />
        </div>
      </Card>
      {down.length > 0 && <Note tone="amber">{down.length} of {d.sources.length} data sources are not answering from this server right now: {down.map((s) => s.label).join(", ")}. Their figures are left out, not estimated.</Note>}
      <Technical d={d} />
      <Card title="Spot buying and selling pressure" sub="Share of traded value where the buyer was the aggressor (bought at the ask). Above 53% = buyers in control, below 47% = sellers." testid="intel-pressure"><Pressure p={d.pressure} /></Card>
      <Derivatives d={d.derivatives} />
      <Liquidations l={d.liquidations} />
      <Correlations c={d.correlations} />
      <Card title="Data sources" sub="Every provider is free and public. Checked continuously; a failed source retries with a growing delay." testid="intel-sources">
        <div className="overflow-x-auto scroll-thin">
          <table className="desk-table">
            <thead><tr><th>Source</th><th>Module</th><th>Status</th><th>Last update</th><th>Detail</th></tr></thead>
            <tbody>{d.sources.map((s) => <tr key={s.key}><td>{s.label}</td><td className="text-slate-400">{s.module}</td>
              <td className={`font-semibold ${s.status === "OK" ? "text-teal-300" : s.status === "WAITING" ? "text-slate-400" : "text-red-300"}`}>{s.status === "OK" ? "Working" : s.status === "WAITING" ? "Starting" : "Unavailable"}</td>
              <td className="font-mono text-[11.5px]">{s.updated ? ago(s.updated) : "—"}</td><td className="text-[11.5px] text-slate-400">{s.error || s.note}</td></tr>)}</tbody>
          </table>
        </div>
        <p className="mt-2 text-[11px] text-slate-500">Coming in the next phases: US macro, spot ETF flows, on-chain, sentiment, crash-risk and recovery scores, forecasts with measured accuracy, the estimated liquidation heatmap and alerts.</p>
      </Card>
    </div>
  );
}
