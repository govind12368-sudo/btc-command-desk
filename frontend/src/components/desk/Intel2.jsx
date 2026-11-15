import React, { useEffect, useState } from "react";
import { toast } from "sonner";
import { api, errText } from "@/lib/api";
import { Card, Note } from "@/components/desk/ui";
import { Field, Input, Btn } from "@/components/desk/forms";
import { n, ist, ago } from "@/lib/fmt";

const sign = (v, d = 2) => (v == null ? "—" : `${v > 0 ? "+" : v < 0 ? "−" : ""}${n(Math.abs(v), d)}`);
const flow = (v) => (v == null ? "—" : `${v > 0 ? "+" : v < 0 ? "−" : ""}$${n(Math.abs(v), 1)}M`);
const tone = (v) => (v == null || v === 0 ? "text-slate-300" : v > 0 ? "text-teal-300" : "text-red-300");
const compact = (v, d = 0) => (v == null ? "—" : Math.abs(v) >= 1e12 ? `${(v / 1e12).toFixed(2)}T` : Math.abs(v) >= 1e9 ? `${(v / 1e9).toFixed(2)}B` : Math.abs(v) >= 1e6 ? `${(v / 1e6).toFixed(2)}M` : n(v, d));
const Wait = ({ what }) => <p className="rounded border border-[#1e2a4a] px-3 py-3 text-[12.5px] text-slate-400">{what}: waiting for the first update. Nothing is estimated in its place.</p>;
const Src = ({ children }) => <p className="mt-2 text-[11px] text-slate-500">{children}</p>;

function Fig({ label, value, sub, cls = "text-slate-50" }) {
  return (
    <div className="rounded-lg border border-[#1e2a4a] bg-[#0f172a]/60 px-3 py-2.5">
      <div className="text-[11.5px] text-slate-400">{label}</div>
      <div className={`font-mono text-lg font-bold ${cls}`}>{value}</div>
      {sub && <div className="text-[11px] text-slate-500">{sub}</div>}
    </div>
  );
}

function Spark({ data, label }) {
  const v = (data || []).map((p) => p.v).filter((x) => x != null);
  if (v.length < 3) return <span className="text-slate-600">—</span>;
  const lo = Math.min(...v), hi = Math.max(...v), w = 96, h = 22;
  const pts = v.map((y, i) => `${((i / (v.length - 1)) * w).toFixed(1)},${(h - 2 - ((y - lo) / (hi - lo || 1)) * (h - 4)).toFixed(1)}`).join(" ");
  return <svg width={w} height={h} role="img" aria-label={label}><polyline points={pts} fill="none" stroke="#94a3b8" strokeWidth="1.5" strokeLinejoin="round" /></svg>;
}

const macroVal = (r, v) => (v == null ? "—" : r.unit === "USD million" ? `$${n(v / 1e6, 3)}T` : r.unit === "USD billion" ? `$${n(v, 1)}B` : r.unit === "thousand jobs" ? `${sign(v, 0)}K` : r.unit === "index" ? n(v, 2) : `${n(v, 2)}%`);
const macroChg = (r) => (r.unit === "USD million" ? `${sign(r.change / 1e3, 1)}B` : r.unit === "USD billion" ? `${sign(r.change, 1)}B` : r.unit === "thousand jobs" ? `${sign(r.change, 0)}K` : sign(r.change, 2));

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
// Monthly and quarterly figures are dated by FRED on the first day of their period: show the period instead.
const period = (r) => { const [y, mo] = (r.date || "").split("-"); return r.freq === "M" ? `${MONTHS[+mo - 1]} ${y}` : r.freq === "Q" ? `Q${Math.floor((+mo - 1) / 3) + 1} ${y}` : r.date; };

export function Macro({ m }) {
  if (!m) return <Card title="US economy and interest rates" testid="intel-macro"><Wait what="Macro data" /></Card>;
  const s = m.summary;
  return (
    <Card title="US economy and interest rates" sub="Slow background forces. They change over weeks and months, not minutes." testid="intel-macro">
      {s && <div className="mb-3 rounded-lg border border-[#1e2a4a] bg-[#0f172a]/60 px-3 py-2.5">
        <div className="text-[14px] font-semibold text-slate-50">{s.label}</div>
        <div className="mt-1 text-[12px] text-slate-400">{s.headwinds} against · {s.tailwinds} in favour. {s.note}</div>
        {s.points.length > 0 && <ul className="mt-1.5 space-y-0.5 text-[12.5px]">{s.points.map((p) => <li key={p.key} className={p.side === "HEADWIND" ? "text-red-300" : "text-teal-300"}>{p.side === "HEADWIND" ? "▼ Against: " : "▲ In favour: "}<span className="text-slate-200">{p.text}</span></li>)}</ul>}
      </div>}
      <div className="overflow-x-auto scroll-thin">
        <table className="desk-table">
          <thead><tr><th>Measure</th><th className="num">Latest</th><th className="num">Before</th><th className="num">Change</th><th>Trend</th><th>Data for</th><th>Why it matters</th></tr></thead>
          <tbody>{m.rows.map((r) => r.status === "OK"
            ? <tr key={r.key}><td className="whitespace-nowrap font-semibold">{r.label}</td><td className="num font-mono">{macroVal(r, r.value)}</td><td className="num font-mono text-slate-400">{macroVal(r, r.previous)}</td>
              <td className="num font-mono text-slate-200">{macroChg(r)}</td><td><Spark data={r.history} label={`${r.label} recent history`} /></td><td className="whitespace-nowrap font-mono text-[11.5px] text-slate-400">{period(r)}</td><td className="min-w-[220px] text-[12px] text-slate-400">{r.why}</td></tr>
            : <tr key={r.key}><td className="font-semibold">{r.label}</td><td colSpan={6} className="text-[12px] text-amber-300">Unavailable: {r.reason}</td></tr>)}</tbody>
        </table>
      </div>
      <Src>Source: {m.source || "FRED (St. Louis Fed)"} · updated {ago(m.updated)}. {m.key_set ? "" : "No FRED key saved: add one in Settings to use the official API."} Change is against the previous release, in the same unit.</Src>
    </Card>
  );
}

export function Etf({ e }) {
  if (!e) return <Card title="Spot Bitcoin ETF flows (US)" testid="intel-etf"><Wait what="ETF flows" /></Card>;
  const hist = e.history || [];
  const max = Math.max(1, ...hist.map((r) => Math.abs(r.total_musd)));
  const funds = Object.entries(e.latest?.funds || {}).map(([k, v]) => [k, parseFloat(String(v).replace(/,/g, ""))]).filter(([, v]) => Number.isFinite(v) && v !== 0).sort((a, b) => Math.abs(b[1]) - Math.abs(a[1]));
  const c = e.cross_check || {};
  return (
    <Card title="Spot Bitcoin ETF flows (US)" sub="Money going into or out of the US spot Bitcoin ETFs each trading day. Inflows mean funds must buy BTC." testid="intel-etf">
      <div className="grid grid-cols-2 gap-2 lg:grid-cols-5">
        <Fig label={`Latest day (${e.latest.date})`} value={flow(e.latest.total_musd)} cls={tone(e.latest.total_musd)} />
        <Fig label="Last 5 trading days" value={flow(e.sum_5d)} cls={tone(e.sum_5d)} sub={e.label} />
        <Fig label="Last 10 trading days" value={flow(e.sum_10d)} cls={tone(e.sum_10d)} />
        <Fig label="Last 20 trading days" value={flow(e.sum_20d)} cls={tone(e.sum_20d)} />
        <Fig label="Current run" value={e.streak.side === "FLAT" ? "—" : `${e.streak.days} day${e.streak.days === 1 ? "" : "s"}`} cls={e.streak.side === "INFLOW" ? "text-teal-300" : e.streak.side === "OUTFLOW" ? "text-red-300" : "text-slate-300"} sub={e.streak.side === "INFLOW" ? "of inflows in a row" : e.streak.side === "OUTFLOW" ? "of outflows in a row" : null} />
      </div>
      {hist.length > 1 && <>
        <div className="mt-3 flex h-24 items-stretch gap-[2px]" role="img" aria-label="Daily net ETF flow, oldest on the left">
          {hist.map((r) => (
            <div key={r.date} className="flex flex-1 flex-col" title={`${r.date}: ${flow(r.total_musd)}`}>
              <div className="flex flex-1 items-end">{r.total_musd > 0 && <div className="w-full rounded-t bg-teal-400" style={{ height: `${(r.total_musd / max) * 100}%`, minHeight: 2 }} />}</div>
              <div className="h-px bg-slate-600" />
              <div className="flex flex-1 items-start">{r.total_musd < 0 && <div className="w-full rounded-b bg-red-400" style={{ height: `${(-r.total_musd / max) * 100}%`, minHeight: 2 }} />}</div>
            </div>))}
        </div>
        <p className="mt-1 text-[11px] text-slate-500"><span className="text-teal-300">■</span> inflow · <span className="text-red-300">■</span> outflow · {hist[0].date} to {hist[hist.length - 1].date} · tallest bar ${n(max, 0)}M · hover a bar for the day</p>
      </>}
      {funds.length > 0 && <p className="mt-2 text-[12px] text-slate-300">Latest day by fund: {funds.slice(0, 8).map(([k, v]) => <span key={k} className="mr-3 whitespace-nowrap">{k} <span className={`font-mono ${tone(v)}`}>{flow(v)}</span></span>)}</p>}
      <p className={`mt-2 text-[12px] ${c.status === "OK" ? (c.mismatches?.length ? "text-amber-300" : "text-teal-300") : "text-slate-400"}`}>
        Second-source check: {c.status === "OK" ? c.text : `${c.reason || "the second source did not answer"}.`}
        {c.mismatches?.length > 0 && ` Examples: ${c.mismatches.map((x) => `${x.date} shown ${flow(x.primary)}, other source ${flow(x.second)}`).join("; ")}.`}
      </p>
      <Src>Source: {e.source} · updated {ago(e.updated)}. {e.note}</Src>
    </Card>
  );
}

export function OnChain({ o, price }) {
  if (!o) return <Card title="On-chain data" testid="intel-onchain"><Wait what="On-chain data" /></Card>;
  const v = o.valuation, mp = o.mempool || {};
  const ok = o.rows.filter((r) => r.status === "OK"), rest = o.rows.filter((r) => r.status !== "OK");
  return (
    <Card title="On-chain data" sub="What is happening on the Bitcoin network itself. Only free data is shown; paid metrics are listed, not guessed." testid="intel-onchain">
      <div className="grid grid-cols-2 gap-2 lg:grid-cols-4">
        <Fig label="MVRV (price vs what holders paid)" value={v ? n(v.mvrv, 2) : "Unavailable"} sub={v ? v.zone : "Coin Metrics not answering"} />
        <Fig label="Average holder cost (calculated)" value={v?.realized_price ? n(v.realized_price, 0) : "—"} sub={v?.realized_price && price ? `price is ${n((price / v.realized_price - 1) * 100, 0)}% above it` : null} />
        <Fig label="Unrealized profit share (NUPL, calculated)" value={v ? `${n(v.nupl * 100, 0)}%` : "—"} sub="share of market value that is paper profit" />
        <Fig label="Network fee now" value={mp.status === "OK" ? `${mp.fee_fast} sat/vB` : "Unavailable"} sub={mp.status === "OK" ? `${n(mp.waiting_tx, 0)} transactions waiting` : mp.reason} />
      </div>
      <div className="mt-3 overflow-x-auto scroll-thin">
        <table className="desk-table">
          <thead><tr><th>Measure</th><th className="num">Latest day</th><th className="num">7-day average</th><th className="num">30-day average</th><th className="num">Week vs month</th><th>Trend (90 days)</th><th>What it tells you</th><th>Source</th></tr></thead>
          <tbody>
            {ok.map((r) => <tr key={r.key}><td className="whitespace-nowrap font-semibold">{r.label}<div className="text-[11px] font-normal text-slate-500">{r.unit} · {r.date}</div></td>
              <td className={`num font-mono ${r.key === "ex_net" ? tone(-r.value) : ""}`}>{r.key === "ex_net" ? sign(r.value, 0) : compact(r.value, r.digits)}</td>
              <td className="num font-mono">{r.avg_7d == null ? (r.sum_7d == null ? "—" : `${sign(r.sum_7d, 0)} in 7 days`) : compact(r.avg_7d, r.digits)}</td><td className="num font-mono text-slate-400">{r.avg_30d == null ? "—" : compact(r.avg_30d, r.digits)}</td>
              <td className="num font-mono text-slate-200">{r.week_vs_month_pct == null ? "—" : `${sign(r.week_vs_month_pct, 1)}%`}</td><td><Spark data={r.history} label={`${r.label} last 90 days`} /></td>
              <td className="min-w-[200px] text-[12px] text-slate-400">{r.why}</td><td className="text-[11.5px] text-slate-500">{r.source}</td></tr>)}
            {rest.map((r) => <tr key={r.key}><td className="font-semibold">{r.label}</td><td colSpan={7} className={`text-[12px] ${r.status === "NEEDS_KEY" ? "text-slate-400" : "text-amber-300"}`}>{r.status === "NEEDS_KEY" ? r.reason : `Unavailable: ${r.reason}`}</td></tr>)}
          </tbody>
        </table>
      </div>
      {mp.status === "OK" && <p className="mt-2 text-[12px] text-slate-300">Mining difficulty: next change about <span className="font-mono">{sign(mp.difficulty_change_pct, 1)}%</span>{mp.retarget_date ? ` around ${ist(mp.retarget_date)}` : ""} ({n(mp.retarget_progress_pct, 0)}% of the period done). Fees: fast {mp.fee_fast}, within an hour {mp.fee_hour}, economy {mp.fee_economy} sat/vB. Source: mempool.space.</p>}
      {v && <Src>{v.note}</Src>}
      <Src>Updated {ago(o.updated)}. {o.note}</Src>
    </Card>
  );
}

export function Sentiment({ s }) {
  if (!s) return <Card title="Sentiment and news" testid="intel-sentiment"><Wait what="Sentiment" /></Card>;
  const f = s.fng, nw = s.news;
  const fc = (v) => (v == null ? "text-slate-300" : v <= 25 ? "text-red-300" : v < 45 ? "text-amber-300" : v <= 55 ? "text-slate-200" : "text-teal-300");
  return (
    <Card title="Sentiment and news" sub="How the crowd feels. Extremes matter more than the middle." testid="intel-sentiment">
      {f ? <>
        <div className="grid grid-cols-2 gap-2 lg:grid-cols-5">
          <Fig label="Fear & Greed today" value={`${f.value} / 100`} cls={fc(f.value)} sub={f.label} />
          <Fig label="Yesterday" value={f.yesterday ?? "—"} cls={fc(f.yesterday)} />
          <Fig label="A week ago" value={f.week_ago ?? "—"} cls={fc(f.week_ago)} />
          <Fig label="7-day average" value={f.avg_7d == null ? "—" : n(f.avg_7d, 0)} cls={fc(f.avg_7d)} />
          <Fig label="30-day average" value={f.avg_30d == null ? "—" : n(f.avg_30d, 0)} cls={fc(f.avg_30d)} />
        </div>
        <div className="mt-2 flex items-center gap-3 text-[12.5px] text-slate-300"><Spark data={f.history} label="Fear and Greed last 60 days" /><span>{f.read}</span></div>
      </> : <Wait what="Fear & Greed index" />}
      {nw && <>
        <h3 className="mb-1 mt-4 text-[13px] font-semibold text-slate-100">News in the last 24 hours</h3>
        <div className="grid grid-cols-2 gap-2 lg:grid-cols-4">
          <Fig label="Headlines read" value={nw.total} />
          <Fig label="High-impact headlines" value={nw.high_impact} cls={nw.high_impact ? "text-amber-300" : "text-slate-50"} />
          <Fig label="Positive / negative wording" value={<span><span className="text-teal-300">{nw.bullish_words}</span> / <span className="text-red-300">{nw.bearish_words}</span></span>} sub={`${nw.neutral} neutral`} />
          <Fig label="Tone" value={<span className="text-[13px]">{nw.tone}</span>} />
        </div>
        {nw.top.length > 0 && <ul className="mt-2 space-y-1 text-[12.5px]">{nw.top.map((x) => <li key={x.link} className="flex gap-2"><span className={`shrink-0 font-semibold ${x.impact === "CRITICAL" ? "text-red-300" : "text-amber-300"}`}>{x.impact === "CRITICAL" ? "Critical" : "High"}</span>
          <a href={x.link} target="_blank" rel="noopener noreferrer" className="text-slate-200 underline decoration-slate-600 underline-offset-2 hover:decoration-amber-400">{x.title}</a><span className="shrink-0 text-[11.5px] text-slate-500">{x.publisher} · {ago(x.time)}</span></li>)}</ul>}
        {nw.categories.length > 0 && <p className="mt-2 text-[11.5px] text-slate-400">Topics: {nw.categories.map(([k, v]) => `${k} ${v}`).join(" · ")}</p>}
        <Src>{nw.note}</Src>
      </>}
      {(s.unavailable || []).map((u) => <p key={u.label} className="mt-1 text-[12px] text-slate-400">{u.label}: {u.reason}</p>)}
      <Src>Sources: {s.fng_source}; {s.news_source}. Updated {ago(s.updated)}.</Src>
    </Card>
  );
}

export function FredKeyCard() {
  const [st, setSt] = useState(null);
  const [key, setKey] = useState("");
  const [busy, setBusy] = useState(false);
  const load = () => api.get("/intel/fred-key").then((r) => setSt(r.data)).catch(() => setSt({ set: false }));
  useEffect(() => { load(); }, []);
  const save = async (value) => {
    setBusy(true);
    try {
      const { data } = await api.put("/intel/fred-key", { key: value });
      setSt(data); setKey(""); load();
      toast.success(data.set ? "FRED key checked and saved. Macro data is refreshing." : "FRED key removed.");
    } catch (e) { toast.error(errText(e)); }
    setBusy(false);
  };
  return (
    <Card title="Market Intelligence: FRED key (US macro data)" testid="fred-key-card" sub="Free key from the St. Louis Fed. It lets the macro panel use the official data service.">
      <p className="text-[12.5px] text-slate-300">Status: {st == null ? "checking…" : st.set ? <b className="text-teal-300">Key saved{st.saved_at ? ` (${ist(st.saved_at)})` : ""}</b> : <b className="text-amber-300">No key saved. The macro panel uses FRED's public download instead, which may be slower or blocked.</b>}</p>
      <ol className="mt-2 list-decimal space-y-0.5 pl-5 text-[12.5px] text-slate-400">
        <li>Open <a className="underline" href="https://fredaccount.stlouisfed.org/apikeys" target="_blank" rel="noopener noreferrer">fredaccount.stlouisfed.org/apikeys</a> and sign in (free account).</li>
        <li>Press "Request API Key", then copy the 32-character key.</li>
        <li>Paste it here and press Save. The key is tested first and is never shown again.</li>
      </ol>
      <div className="mt-3 flex flex-wrap items-end gap-2">
        <div className="w-full max-w-sm"><Field label="FRED API key"><Input data-testid="fred-key-input" type="password" autoComplete="off" placeholder="32 characters" value={key} onChange={(e) => setKey(e.target.value)} /></Field></div>
        <Btn data-testid="fred-key-save" disabled={busy || !key.trim()} onClick={() => save(key)}>{busy ? "Checking…" : "Check and save"}</Btn>
        {st?.set && <Btn tone="ghost" disabled={busy} onClick={() => save("")}>Remove key</Btn>}
      </div>
      <Note>The key stays on your server and is only sent to the St. Louis Fed.</Note>
    </Card>
  );
}
