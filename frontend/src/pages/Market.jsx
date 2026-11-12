import React from "react";
import { useDesk } from "@/context/DeskContext";
import { useFetch } from "@/hooks/useFetch";
import { Card, Chip, KV, PageHead, Empty, Note } from "@/components/desk/ui";
import { n, usd, ist, sgn, ago } from "@/lib/fmt";
import { LineChart, SignedBars, VIZ } from "@/components/desk/charts";

function OiCharts({ funding }) {
  const { data } = useFetch("/oi-history?hours=24", 60000);
  const pts = (data?.points || []).map((p) => ({ t: p.t, v: p.oi_btc }));
  const first = pts[0]?.v, last = pts[pts.length - 1]?.v;
  const ch = pts.length > 1 && first && last ? (last / first - 1) * 100 : null;
  const fund = [...(funding || [])].sort((a, b) => String(a.time).localeCompare(String(b.time))).map((f) => ({ t: f.time, v: (f.realized_fraction ?? f.funding_fraction ?? 0) * 10000 }));
  return (
    <div className="grid gap-4 lg:grid-cols-[1.4fr_1fr]">
      <Card title="Open interest · last 24 h" sub="OKX BTC-USDT-SWAP, stored by this server every ~5 min" testid="oi-chart"
        right={ch != null && <span className={`rounded px-1.5 py-0.5 font-mono text-[12px] ${ch >= 0 ? "bg-teal-500/10 text-teal-300" : "bg-red-500/10 text-red-300"}`}>{ch >= 0 ? "▲" : "▼"} {sgn(ch, 2)}%</span>}>
        <LineChart series={[{ name: "OI (BTC)", color: VIZ.s1, points: pts }]} yFmt={(v) => n(v, 0)} unit=" BTC" />
      </Card>
      <Card title="Funding settlements" sub="OKX, basis points per 8 h · positive = longs pay shorts" testid="funding-chart">
        <SignedBars points={fund} fmt={(v) => sgn(v, 2)} unit=" bps" />
      </Card>
    </div>
  );
}

function Book({ b }) {
  if (!b) return <Empty>Order book unavailable</Empty>;
  const max = Math.max(...b.bids.map((x) => x[1]), ...b.asks.map((x) => x[1]), 1);
  const Row = ({ p, q, side }) => (
    <div className="relative flex justify-between px-2 py-0.5 font-mono text-[11.5px]">
      <div className={`absolute inset-y-0 ${side === "bid" ? "right-0 bg-teal-500/10" : "left-0 bg-red-500/10"}`} style={{ width: `${(q / max) * 100}%` }} />
      <span className={side === "bid" ? "text-teal-300" : "text-red-300"}>{n(p, 1)}</span><span className="text-slate-300">{n(q, 0)}</span>
    </div>
  );
  return (
    <div data-testid="order-book">
      <div className="flex justify-between px-2 font-mono text-[10px] text-slate-500"><span>price USD</span><span>size (contracts, 0.001 BTC)</span></div>
      {[...b.asks].reverse().map(([p, q]) => <Row key={`a${p}`} p={p} q={q} side="ask" />)}
      <div className="my-1 border-t border-amber-500/30" />
      {b.bids.map(([p, q]) => <Row key={`b${p}`} p={p} q={q} side="bid" />)}
      <p className="mt-2 font-mono text-[10.5px] text-slate-500">{b.source} · exchange time {ist(b.event_time)}</p>
    </div>
  );
}

export default function Market() {
  const { state: s } = useDesk();
  const { data: m } = useFetch("/market", 3000);
  const t = m?.ticker;
  const p = m?.product;
  return (
    <div className="space-y-4">
      <PageHead title="Live Market" sub="Execution market: Delta Exchange India BTCUSD perpetual (USD-quoted, 1 contract = 0.001 BTC). OKX BTC-USDT-SWAP shown separately as derivatives context (USDT)." />
      <div className="grid gap-4 lg:grid-cols-3">
        <Card title="Delta BTCUSD quote" testid="delta-quote" right={<Chip status={m?.ws?.status === "connected" ? "PASS" : "WAIT"} label={`WS ${m?.ws?.status || "—"}`} />}>
          <KV k="Last (USD)" v={n(t?.last, 1)} testid="mkt-last" /><KV k="Best bid / ask" v={`${n(m?.quote?.bid, 1)} / ${n(m?.quote?.ask, 1)}`} />
          <KV k="Mark / spot index" v={`${n(t?.mark, 1)} / ${n(t?.spot_index, 1)}`} /><KV k="Mark basis" v={sgn(t?.mark_basis * 100, 3) + "%"} />
          <KV k="Funding (Delta, per 8h)" v={`${sgn(t?.funding_rate_pct_8h, 4)}% = ${sgn(t?.funding_rate_pct_8h / 100, 6)} fraction`} />
          <KV k="Open interest" v={`${n(t?.oi_btc, 1)} BTC · ${usd(t?.oi_usd, 0)}`} /><KV k="24h high / low" v={`${n(t?.high_24h, 1)} / ${n(t?.low_24h, 1)}`} />
          <KV k="24h turnover" v={usd(t?.turnover_24h_usd, 0)} />
          <p className="mt-2 font-mono text-[10.5px] text-slate-500">quote source {m?.quote?.source} · exchange time {ist(m?.quote?.event_time)} · WS heartbeat {ago(m?.ws?.last_heartbeat)} · reconnects {m?.ws?.reconnects}</p>
        </Card>
        <Card title="Order book (top 15)" testid="book-card"><Book b={m?.book} /></Card>
        <Card title="Trade tape" sub="Delta all_trades WebSocket · taker side" testid="trade-tape">
          <div className="max-h-[420px] overflow-y-auto scroll-thin">
            {m?.trades?.length ? m.trades.map((x, i) => (
              <div key={i} className="flex justify-between font-mono text-[11.5px] py-0.5"><span className={x.side === "buy" ? "text-teal-300" : "text-red-300"}>{x.side.toUpperCase()}</span><span>{n(x.price, 1)}</span><span className="text-slate-400">{x.size} ct</span><span className="text-slate-500">{ist(x.time, false)}</span></div>
            )) : <Empty>Waiting for trades…</Empty>}
          </div>
        </Card>
      </div>
      <div className="grid gap-4 lg:grid-cols-2">
        <Card title="Contract scanner" sub={m?.scanner_note} testid="contract-scanner" right={<Chip status={p?.state === "live" ? "PASS" : "UNAVAILABLE"} label={p?.state?.toUpperCase() || "UNAVAILABLE"} />}>
          {p ? (<>
            <KV k="Symbol / product id" v={`${p.symbol} / ${p.product_id}`} /><KV k="Contract type" v={p.contract_type} />
            <KV k="Underlying · quoting · settling" v={`${p.underlying} · ${p.quoting} · ${p.settling}`} /><KV k="Contract value" v={`${p.contract_value} ${p.contract_unit_currency} per contract`} />
            <KV k="Tick size" v={`${p.tick_size} USD`} /><KV k="Taker / maker fee" v={`${p.taker_fee} / ${p.maker_fee}`} />
            <KV k="Funding interval" v={`${(p.funding_interval_sec || 0) / 3600} h`} /><KV k="Spec retrieved" v={ist(m.product_retrieved)} />
          </>) : <Empty>Contract specification unavailable</Empty>}
        </Card>
        <Card title="OKX BTC-USDT-SWAP context" sub="Separate instrument (USDT-margined). Not the execution market." testid="okx-context">
          <KV k="Last (USDT)" v={n(m?.okx?.last, 1)} /><KV k="Bid / ask (USDT)" v={`${n(m?.okx?.bid, 1)} / ${n(m?.okx?.ask, 1)}`} />
          <KV k="Open interest" v={`${n(m?.okx?.oi_btc, 0)} BTC · ${usd(m?.okx?.oi_usd, 0)}`} />
          <KV k="Funding (fraction / 8h)" v={`${sgn(m?.okx?.funding_fraction, 6)} = ${sgn((m?.okx?.funding_fraction || 0) * 10000, 2)} bps`} />
          <KV k="Next funding" v={ist(m?.okx?.next_funding_time)} /><KV k="Contract value" v={`${m?.okx?.ct_val_btc} BTC`} />
          <KV k="Delta − OKX price gap" v={t?.last && m?.okx?.last ? `${sgn(t.last - m.okx.last, 1)} (USD vs USDT — not a pure basis)` : "—"} />
          <p className="mt-2 font-mono text-[10.5px] text-slate-500">ticker time {ist(m?.okx?.ticker_time)} · OI time {ist(m?.okx?.oi_time)}</p>
        </Card>
      </div>
      <OiCharts funding={m?.okx?.funding_history} />
      <Card title="OI history (stored snapshots, every ~5 min)" testid="oi-history">
        <div className="max-h-[300px] overflow-auto scroll-thin">
          <table className="desk-table"><thead><tr><th>Stored (IST)</th><th>Venue</th><th>Instrument</th><th>OI BTC</th><th>OI USD</th><th>Funding fraction</th></tr></thead>
            <tbody>{(m?.oi_history || []).map((r, i) => <tr key={i}><td className="font-mono">{ist(r.t)}</td><td>{r.venue}</td><td className="font-mono">{r.instrument}</td><td className="font-mono">{n(r.oi_btc, 1)}</td><td className="font-mono">{usd(r.oi_usd, 0)}</td><td className="font-mono">{sgn(r.funding_fraction, 6)}</td></tr>)}</tbody></table>
        </div>
      </Card>
      <Card title="Timeframe alignment" testid="mtf-table">
        <div className="overflow-x-auto scroll-thin"><table className="desk-table"><thead><tr><th>TF</th><th>Trend</th><th>Close</th><th>EMA20</th><th>EMA50</th><th>VWAP</th><th>Vol ratio</th><th>Closed</th></tr></thead>
          <tbody>{s?.mtf?.rows?.map((r) => <tr key={r.tf}><td className="font-mono text-amber-300">{r.tf}</td><td><Chip status={r.trend} /></td><td className="font-mono">{n(r.close, 1)}</td><td className="font-mono">{n(r.ema20, 1)}</td><td className="font-mono">{n(r.ema50, 1)}</td><td>{r.above_vwap == null ? "—" : r.above_vwap ? "above" : "below"}</td><td className="font-mono">{n(r.volume_ratio, 2)}</td><td className="font-mono text-[11px]">{ist(r.bar_close)}</td></tr>)}</tbody></table></div>
      </Card>
      <Note>Contracts, BTC quantity and USD notional are shown separately. Delta funding is published as a percent per 8h; OKX as a fraction per 8h.</Note>
    </div>
  );
}
