import React, { useEffect, useState } from "react";
import { toast } from "sonner";
import { useFetch } from "@/hooks/useFetch";
import { api, errText } from "@/lib/api";
import { Card, PageHead, Note } from "@/components/desk/ui";
import { Btn, Field, Input } from "@/components/desk/forms";
import { n, signedUsd } from "@/lib/fmt";
import { LineChart, VIZ } from "@/components/desk/charts";

const tone = (v) => (v > 0 ? "text-teal-300" : v < 0 ? "text-red-300" : "text-slate-300");
const pct = (v, d = 1) => (v == null ? "—" : `${(v * 100).toFixed(d)}%`);
const STATUS_TONE = { OPEN: "text-sky-300", CLOSED: "text-slate-200", SKIPPED: "text-slate-400", MISSED: "text-amber-300" };
const usd0 = (v) => `$${n(v, 0)}`;

function Stat({ label, value, sub, cls = "text-slate-50" }) {
  return (
    <div className="rounded-lg border border-[#1e2a4a] bg-[#0f172a]/60 px-3 py-2.5">
      <div className="text-[11.5px] text-slate-400">{label}</div>
      <div className={`font-mono text-lg font-bold ${cls}`}>{value}</div>
      {sub && <div className="text-[11px] text-slate-500">{sub}</div>}
    </div>
  );
}

function legsText(legs) {
  return (legs || []).map((l) => `${l.side} ${l.sym} ${l.qty != null ? `${l.qty.toFixed(3)} BTC ` : ""}@ ${n(l.fill, 1)}${l.exit ? ` → ${l.exit} ${n(l.exit_price, 1)}${l.stop_ist ? ` (${l.stop_ist})` : ""}` : ""}`).join("  ·  ");
}

function RealData({ rows, notChecked }) {
  return (
    <Card title="Real-data backtest on Delta Exchange India" sub="Every trade rebuilt from Delta's own mark prices, official settlement index and fee record, including the settlement fee on options that expire in the money." testid="opt-realdata">
      <div className="overflow-x-auto scroll-thin">
        <table className="desk-table">
          <thead><tr><th>Size</th><th>Period</th><th className="num">Trades</th><th className="num">Win %</th><th className="num">Gross</th><th className="num">Fees</th><th className="num">Net</th><th className="num">Return</th><th className="num">Max DD</th><th className="num">Worst trade</th><th className="num">Net 2024</th><th className="num">Net 2025</th><th className="num">Net 2026</th><th>Verdict</th></tr></thead>
          <tbody>{rows.map((r) => (
            <tr key={r.period}>
              <td className="text-[12px]">{r.size}</td><td className="text-[12px]">{r.period}</td>
              <td className="num">{r.trades}<div className="text-[11px] text-slate-500">{r.wins} won · {r.losses} lost</div></td><td className="num">{pct(r.win_rate)}</td>
              <td className={`num ${tone(r.gross)}`}>{signedUsd(r.gross, 0)}</td><td className="num">{signedUsd(-r.fees, 0)}</td>
              <td className={`num font-semibold ${tone(r.net)}`}>{signedUsd(r.net, 0)}</td><td className={`num ${tone(r.return)}`}>{r.return >= 0 ? "+" : ""}{pct(r.return, 0)}</td>
              <td className="num text-red-300">{signedUsd(-r.max_dd, 0)}<div className="text-[11px] text-slate-500">{pct(r.max_dd_pct)}</div></td>
              <td className="num text-red-300">{signedUsd(r.worst, 0)}</td>
              {["2024", "2025", "2026"].map((y) => <td key={y} className={`num ${tone(r.years[y])}`}>{r.years[y] ? signedUsd(r.years[y], 0) : "—"}</td>)}
              <td className="max-w-[320px] text-[12px] text-slate-300">{r.verdict || "—"}</td>
            </tr>))}</tbody>
        </table>
      </div>
      <p className="mt-2 text-[11.5px] text-slate-500">{notChecked}</p>
    </Card>
  );
}

function Sizing({ d, reload }) {
  const [v, setV] = useState(d.sizing);
  const [busy, setBusy] = useState(false);
  useEffect(() => { setV(d.sizing); }, [d.sizing]);
  const save = async () => {
    setBusy(true);
    try { await api.post("/option-picks/sizing", Object.fromEntries(Object.entries(v).map(([k, x]) => [k, Number(x)]))); toast.success("Size saved: every trade re-sized"); await reload(); }
    catch (e) { toast.error(errText(e)); } finally { setBusy(false); }
  };
  const f = (k, label) => <Field label={label}><Input type="number" value={v[k]} onChange={(e) => setV({ ...v, [k]: e.target.value })} className="w-28" data-testid={`opt-${k}`} /></Field>;
  return (
    <Card title="Size per leg (admin)" sub="Capital × leverage per leg. Saving re-sizes the backtest and every paper trade from their stored prices." testid="opt-sizing">
      <div className="flex flex-wrap items-end gap-3">
        {f("put_capital", "Put capital ($)")}{f("put_leverage", "Put leverage (x)")}{f("call_capital", "Call capital ($)")}{f("call_leverage", "Call leverage (x)")}
        <Btn tone="gold" disabled={busy} onClick={save}>Save size</Btn>
      </div>
    </Card>
  );
}

function StrategyBacktest({ s, b, historyTo, sizeLabel }) {
  const [limit, setLimit] = useState(60);
  if (!b) return <Note>Working out the backtest…</Note>;
  const st = b.stats;
  let eq = s.capital;
  const curve = [...b.trades].filter((r) => r.net != null).reverse().map((r) => ({ t: r.day, v: (eq += r.net) }));
  const download = async () => {
    try {
      const r = await api.get(`/option-picks/trades.csv?key=${s.key}`, { responseType: "blob" });
      const u = URL.createObjectURL(r.data);
      const a = document.createElement("a");
      a.href = u; a.download = `option_picks_${s.key}_backtest.csv`; a.click();
      setTimeout(() => URL.revokeObjectURL(u), 2000);
    } catch (e) { toast.error(errText(e)); }
  };
  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 gap-2 sm:grid-cols-4 lg:grid-cols-8">
        <Stat label="Trades" value={st.trades} sub={`${st.wins} won · ${st.losses} lost`} />
        <Stat label="Win ratio" value={pct(st.win_rate)} sub="net after fees > 0" />
        <Stat label="Gross P&L" value={signedUsd(st.gross, 0)} cls={tone(st.gross)} />
        <Stat label="Fees + GST" value={signedUsd(-st.fees, 0)} cls="text-slate-300" />
        <Stat label="Net P&L" value={signedUsd(st.net, 0)} cls={tone(st.net)} sub={`${pct(st.return, 0)} on ${usd0(s.capital)}`} />
        <Stat label="Max drawdown" value={signedUsd(-st.max_dd, 0)} cls="text-red-300" sub={`${pct(st.max_dd_pct)} · lowest balance ${usd0(st.lowest_balance)}`} />
        <Stat label="Worst trade" value={signedUsd(st.worst, 0)} cls="text-red-300" sub={`best ${signedUsd(st.best, 0)}`} />
        <Stat label="With 5% worse fills + 2x fees" value={signedUsd(st.view2_net, 0)} cls={tone(st.view2_net)} sub={`real trades at fill on ${pct(st.real_fill, 0)} of days`} />
      </div>
      {curve.length > 1 && (
        <Card title="Account balance" sub={`From ${usd0(s.capital)} · ${sizeLabel} · backtest 1 Jan 2024 → ${historyTo}, then the server's own daily backtest`}>
          <LineChart series={[{ name: "Balance $", color: VIZ.s1, points: curve }]} xFmt={(v) => v} yFmt={(v) => n(v, 0)} unit=" $" height={150} />
        </Card>
      )}
      <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
        <Card title="Yearly" sub="Net after fees">
          <table className="desk-table">
            <thead><tr><th>Year</th><th className="num">Trades</th><th className="num">Win %</th><th className="num">Gross</th><th className="num">Fees</th><th className="num">Net</th><th className="num">Max DD</th></tr></thead>
            <tbody>{b.yearly.map((y) => (
              <tr key={y.year} className={y.year === "Total till date" ? "border-t-2 border-[#2a3a66] font-semibold" : ""}>
                <td>{y.year}</td><td className="num">{y.trades}</td><td className="num">{pct(y.win_rate)}</td><td className={`num ${tone(y.gross)}`}>{signedUsd(y.gross, 0)}</td>
                <td className="num">{signedUsd(-y.fees, 0)}</td><td className={`num font-semibold ${tone(y.net)}`}>{signedUsd(y.net, 0)}</td><td className="num text-red-300">{signedUsd(-y.max_dd, 0)}</td>
              </tr>))}</tbody>
          </table>
        </Card>
        <Card title="Monthly" sub="Newest first. Losing months are red.">
          <div className="max-h-[330px] overflow-auto scroll-thin">
            <table className="desk-table">
              <thead className="sticky top-0 bg-[#0b1224]"><tr><th>Month</th><th className="num">Trades</th><th className="num">Win %</th><th className="num">Net</th><th className="num">Cumulative</th></tr></thead>
              <tbody>{[...b.monthly].reverse().map((m) => (
                <tr key={m.month} className={m.net < 0 ? "bg-red-500/10" : ""}><td className={`font-mono ${m.net < 0 ? "text-red-300" : ""}`}>{m.month}</td><td className="num">{m.trades}</td>
                  <td className="num">{pct(m.win_rate)}</td><td className={`num font-semibold ${m.net < 0 ? "text-red-400" : tone(m.net)}`}>{signedUsd(m.net, 0)}</td><td className={`num ${tone(m.cumulative)}`}>{signedUsd(m.cumulative, 0)}</td></tr>))}</tbody>
            </table>
          </div>
        </Card>
      </div>
      <Card title="Backtest trades" sub={`Newest first · ${b.trades_total} trades · ${sizeLabel}`} right={<Btn tone="ghost" onClick={download}>Download all (CSV)</Btn>}>
        <div className="overflow-x-auto scroll-thin">
          <table className="desk-table">
            <thead><tr><th>Expiry</th><th>Entry (IST)</th><th className="num">BTC futures</th><th className="num">Qty per leg</th><th>Legs: sell fill → exit</th><th className="num">Settlement</th><th className="num">Gross</th><th className="num">Fees</th><th className="num">Net</th><th>Real fill</th></tr></thead>
            <tbody>{b.trades.filter((r) => r.net != null).slice(0, limit).map((r) => (
              <tr key={r.day + r.entry_ist}><td className="font-mono">{r.day}</td><td className="font-mono">{r.entry_ist.slice(11)}</td><td className="num">{n(r.price, 1)}</td><td className="num">{r.qty != null ? r.qty.toFixed(3) : "—"}</td>
                <td className="text-[11.5px]">{legsText(r.legs)}</td><td className="num">{n(r.settle, 1)}</td><td className={`num ${tone(r.gross)}`}>{signedUsd(r.gross)}</td>
                <td className="num">{signedUsd(-r.fees)}</td><td className={`num font-semibold ${tone(r.net)}`}>{signedUsd(r.net)}</td><td>{r.real ? "Yes" : "No"}</td></tr>))}</tbody>
          </table>
          {b.trades.length > limit && <div className="p-2 text-center"><Btn tone="ghost" onClick={() => setLimit(limit + 60)}>Show more</Btn></div>}
        </div>
      </Card>
    </div>
  );
}

function legLive(l) {
  const fill = l.real === true ? "real trade at our price ✓" : l.real === false ? "no real trade at our price" : "fill check pending";
  const exit = l.exit ? ` → ${l.exit} ${n(l.exit_price, 1)}${l.stop_ist ? ` (${l.stop_ist})` : ""}` : l.stop ? ` · stop ${n(l.stop, 1)} · high so far ${n(l.high, 1)}` : "";
  return `${l.side} ${l.sym} ${l.qty != null ? `${l.qty.toFixed(3)} BTC ` : ""}· mark ${n(l.mark, 1)} · best bid ${l.bid ?? "—"} → sold ${n(l.fill, 1)}${exit} · ${fill}`;
}

function Live({ d }) {
  const live = d.live || [];
  return (
    <div className="space-y-4">
      <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
        {d.enabled.map((k) => {
          const s = d.strategies.find((x) => x.key === k);
          const ls = d.live_stats[k] || {};
          return (
            <div key={k} className="rounded-lg border border-[#1e2a4a] bg-[#0f172a]/60 px-3 py-2.5 text-[12px]" data-testid="opt-live-status">
              <div className="font-semibold text-slate-100">Pick {s.pick} · {s.name}</div>
              <div className="text-slate-400">Next paper entry: {d.next_entry[k] || "—"}</div>
              <div className="text-slate-300">Paper: {ls.trades || 0} closed · {ls.wins || 0} won · net <span className={tone(ls.net)}>{signedUsd(ls.net || 0)}</span> · balance {usd0(s.capital + (ls.net || 0))} from {usd0(s.capital)}</div>
            </div>
          );
        })}
      </div>
      <Card title="Live paper trades" sub={`Size: ${d.size_label} · about ${usd0(d.order_notional)} sold per order`} testid="opt-live">
        <p className="-mt-1 mb-2 text-[11.5px] text-slate-500">Opened at the live Delta mark at the end of the entry minute; each leg stopped on the 1-minute mark; settled at Delta's official settlement index. No orders are sent.</p>
        <div className="overflow-x-auto scroll-thin">
          <table className="desk-table">
            <thead><tr><th>Expiry</th><th>Status</th><th>Entry (IST)</th><th className="num">BTC futures</th><th className="num">Qty per leg</th><th>Legs</th><th className="num">Gross</th><th className="num">Fees</th><th className="num">Net</th><th>Note</th></tr></thead>
            <tbody>{live.map((t) => (
              <tr key={t.id}><td className="font-mono">{t.day}</td><td className={STATUS_TONE[t.status] || ""}>{t.status}</td><td className="font-mono">{t.entry_ist ? t.entry_ist.slice(11) : "—"}</td>
                <td className="num">{n(t.price, 1)}</td><td className="num">{t.qty != null ? t.qty.toFixed(3) : "—"}</td>
                <td className="text-[11.5px]">{(t.legs || []).map((l) => <div key={l.sym}>{legLive(l)}</div>)}</td>
                <td className={`num ${tone(t.gross)}`}>{t.gross != null ? signedUsd(t.gross) : "—"}</td><td className="num">{t.fees != null ? signedUsd(-t.fees) : "—"}</td>
                <td className={`num font-semibold ${tone(t.net)}`}>{t.net != null ? signedUsd(t.net) : t.status === "OPEN" ? "open" : "—"}</td><td className="text-[12px] text-slate-400">{t.reason || ""}</td></tr>))}</tbody>
          </table>
          {!live.length && <div className="p-4 text-center text-xs text-slate-500">No paper trades yet.</div>}
        </div>
      </Card>
      <Card title="Paper vs backtest" sub="Each paper day next to the server's own backtest of the same expiry (worked out about 10 minutes after the 17:30 settlement), both at the current size." testid="opt-compare">
        <div className="overflow-x-auto scroll-thin">
          <table className="desk-table">
            <thead><tr><th>Expiry</th><th>Paper</th><th className="num">Paper net</th><th>Backtest</th><th className="num">Backtest net</th></tr></thead>
            <tbody>{d.compare.map((c) => (
              <tr key={c.day + c.key}><td className="font-mono">{c.day}</td><td title={c.live_reason || ""}>{c.live_status}</td><td className={`num ${tone(c.live_net)}`}>{c.live_net != null ? signedUsd(c.live_net) : "—"}</td>
                <td title={c.bt_reason || ""}>{c.bt_status}</td><td className={`num ${tone(c.bt_net)}`}>{c.bt_net != null ? signedUsd(c.bt_net) : "—"}</td></tr>))}</tbody>
          </table>
          {!d.compare.length && <div className="p-4 text-center text-xs text-slate-500">Nothing to compare yet.</div>}
        </div>
      </Card>
    </div>
  );
}

export default function OptionPicks() {
  const { data: d, error, reload } = useFetch("/option-picks", 30000);
  const [tab, setTab] = useState("live");
  useEffect(() => { document.title = "Option Picks"; }, []);
  if (error && !d) return <Note tone="red">Could not load Option Picks: {String(error)}</Note>;
  if (!d) return <div className="text-sm text-slate-400">Loading Option Picks…</div>;
  const btn = (on) => `rounded px-3 py-1.5 text-xs font-semibold ${on ? "bg-amber-500 text-slate-950" : "border border-[#1e2a4a] text-slate-300 hover:text-white"}`;
  const s = d.strategies[0];
  const z = d.sizing;
  const same = z.put_capital === z.call_capital && z.put_leverage === z.call_leverage;
  const sub = `Pick 5: short ATM BTC straddle at 17:00 IST on Delta Exchange India (expiry 17:30 IST) · ${same
    ? `$${n(z.put_capital, 0)} × ${z.put_leverage}x per leg (put + call), about $${n(z.put_capital * z.put_leverage, 0)} of BTC per leg` : d.size_label} · paper only`;
  return (
    <div className="space-y-4" data-testid="option-picks-page">
      <PageHead title="Option Picks" sub={sub} />
      <Note>Paper only: no orders are sent and no alerts.</Note>
      {d.error && <Note tone="red">Last update failed: {d.error}</Note>}
      {s && d.backtest[s.key] && <RealData rows={d.backtest[s.key].realdata} notChecked={d.not_checked} />}
      {s && (
        <div className="rounded-lg border border-[#1e2a4a] bg-[#0f172a]/60 px-3 py-2 text-[12px]">
          <div className="font-semibold text-slate-100">Pick {s.pick} · {s.name} <span className="font-mono text-[11px] font-normal text-slate-500">{s.code}</span></div>
          <div className="text-slate-300">Sell the ATM put and ATM call (same strike, nearest to BTC futures) at the close of the 17:00 IST minute, filled {s.live_entry_ist} IST, at mark − max($0.10, 1%). Skip the day if the put mark is below $20 or BTC is up more than 2% since 17:31 IST yesterday. Each leg is bought back when its 1-minute mark high reaches 2× its sell price; the other leg stays open; otherwise both settle at 17:30 IST.</div>
        </div>
      )}
      {d.is_admin && <Sizing d={d} reload={reload} />}
      <div className="flex flex-wrap gap-2">
        <button className={btn(tab === "live")} onClick={() => setTab("live")} data-testid="opt-tab-live">Live paper trading</button>
        {s && <button className={btn(tab === s.key)} onClick={() => setTab(s.key)} data-testid={`opt-tab-${s.key}`}>Backtest journal</button>}
      </div>
      {tab === "live" ? <Live d={d} /> : s && <StrategyBacktest s={s} b={d.backtest[s.key]} historyTo={d.history_to} sizeLabel={d.size_label} />}
    </div>
  );
}
