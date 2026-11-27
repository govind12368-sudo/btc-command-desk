import React, { useEffect, useState } from "react";
import { Link } from "react-router-dom";
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
const dmy = (iso) => (iso ? new Date(`${iso}T00:00:00`).toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric" }) : "—");

function Stat({ label, value, sub, cls = "text-slate-50" }) {
  return (
    <div className="rounded-lg border border-line bg-panel2/60 px-3 py-2.5">
      <div className="text-[11.5px] text-slate-400">{label}</div>
      <div className={`font-mono text-lg font-bold ${cls}`}>{value}</div>
      {sub && <div className="text-[11px] text-slate-500">{sub}</div>}
    </div>
  );
}

// The same totals strip as Elite 4 Edge: trades, win ratio, gross, fees, net and (admin) max drawdown.
function Totals({ s, viewer, testid = "opt-totals" }) {
  if (!s) return null;
  const per = s.trades ? s.net / s.trades : null;
  return (
    <div className={`grid grid-cols-2 gap-2 sm:grid-cols-3 ${viewer ? "lg:grid-cols-5" : "lg:grid-cols-6"}`} data-testid={testid}>
      <Stat label="Trades (closed)" value={s.trades} sub={`${s.wins} won · ${s.losses} lost`} />
      <Stat label="Win ratio" value={pct(s.win_rate, 0)} sub="net after fees > 0" />
      <Stat label="Gross P&L" value={signedUsd(s.gross)} cls={tone(s.gross)} sub="before fees" />
      <Stat label="Fees" value={signedUsd(-s.fees)} cls="text-slate-300" sub="Delta option fees + 18% GST" />
      <Stat label="Net P&L" value={signedUsd(s.net)} cls={tone(s.net)} sub={per != null ? `${signedUsd(per)} a trade` : null} />
      {!viewer && <Stat label="Max drawdown" value={signedUsd(-(s.max_dd || 0))} cls={s.max_dd ? "text-red-300" : "text-slate-300"}
        sub={`closed trades, from the peak${s.max_dd_pct ? ` · ${pct(s.max_dd_pct, 1)} of ${s.compounded ? "the peak" : "the capital"}` : ""}`} />}
    </div>
  );
}

function legsText(legs) {
  return (legs || []).map((l) => `${l.side} ${l.sym} ${l.qty != null ? `${l.qty.toFixed(3)} BTC ` : ""}@ ${n(l.fill, 1)}${l.exit ? ` → ${l.exit} ${n(l.exit_price, 1)}${l.stop_ist ? ` (${l.stop_ist})` : ""}` : ""}`).join("  ·  ");
}

function RealData({ rows, notChecked, viewer }) {
  return (
    <Card title={viewer ? "Backtest results" : "Real-data backtest on Delta Exchange India"} sub={viewer ? "Net after fees. Backtest, not live trades." : "Every trade rebuilt from Delta's own mark prices, official settlement index and fee record, including the settlement fee on options that expire in the money."} testid="opt-realdata">
      <div className="overflow-x-auto scroll-thin">
        <table className="desk-table">
          <thead><tr>{!viewer && <th>Size</th>}<th>Period</th><th className="num">Trades</th><th className="num">Win %</th><th className="num">Gross</th><th className="num">Fees</th><th className="num">Net</th><th className="num">Return</th><th className="num">Net 2024</th><th className="num">Net 2025</th><th className="num">Net 2026</th>{!viewer && <th>Verdict</th>}</tr></thead>
          <tbody>{rows.map((r) => (
            <tr key={r.period}>
              {!viewer && <td className="text-[12px]">{r.size}</td>}<td className="whitespace-nowrap text-[12px]">{r.period}</td>
              <td className="num">{r.trades}<div className="text-[11px] text-slate-500">{r.wins} won · {r.losses} lost</div></td><td className="num">{pct(r.win_rate)}</td>
              <td className={`num ${tone(r.gross)}`}>{signedUsd(r.gross, 0)}</td><td className="num">{signedUsd(-r.fees, 0)}</td>
              <td className={`num font-semibold ${tone(r.net)}`}>{signedUsd(r.net, 0)}</td><td className={`num ${tone(r.return)}`}>{r.return >= 0 ? "+" : ""}{pct(r.return, 0)}</td>
              {["2024", "2025", "2026"].map((y) => <td key={y} className={`num ${tone(r.years[y])}`}>{r.years[y] ? signedUsd(r.years[y], 0) : "—"}</td>)}
              {!viewer && <td className="max-w-[320px] text-[12px] text-slate-300">{r.verdict || "—"}</td>}
            </tr>))}</tbody>
        </table>
      </div>
      {!viewer && notChecked && <p className="mt-2 text-[11.5px] text-slate-500">{notChecked}</p>}
    </Card>
  );
}

function Sizing({ d, reload }) {
  const [v, setV] = useState(d.sizing);
  const [busy, setBusy] = useState(false);
  useEffect(() => { setV(d.sizing); }, [d.sizing]);
  const save = async () => {
    setBusy(true);
    try {
      const body = Object.fromEntries(["put_capital", "put_leverage", "call_capital", "call_leverage"].map((k) => [k, Number(v[k])]));
      await api.post("/option-picks/sizing", { ...body, compound: Number(v.compound) === 1 });
      toast.success("Size saved: every trade re-sized"); await reload();
    } catch (e) { toast.error(errText(e)); } finally { setBusy(false); }
  };
  const f = (k, label) => <Field label={label}><Input type="number" value={v[k]} onChange={(e) => setV({ ...v, [k]: e.target.value })} className="w-28" data-testid={`opt-${k}`} /></Field>;
  return (
    <Card title="Size per leg (admin)" sub="Capital × leverage per leg. Saving re-sizes the backtest and every paper trade from their stored prices." testid="opt-sizing">
      <div className="flex flex-wrap items-end gap-3">
        {f("put_capital", "Put capital ($)")}{f("put_leverage", "Put leverage (x)")}{f("call_capital", "Call capital ($)")}{f("call_leverage", "Call leverage (x)")}
        <Field label="Capital">
          <select value={Number(v.compound) === 1 ? "1" : "0"} onChange={(e) => setV({ ...v, compound: Number(e.target.value) })} data-testid="opt-compound"
            className="h-9 rounded-md border border-line bg-panel2 px-2 text-[13px] text-slate-100">
            <option value="1">Compounded ★ Recommended</option>
            <option value="0">Fixed</option>
          </select>
        </Field>
        <Btn tone="gold" disabled={busy} onClick={save}>Save size</Btn>
      </div>
      <p className="mt-2 text-[11.5px] text-slate-500">Compounded: each leg gets its share of the balance after the last closed trade (put share = put capital ÷ both capitals). Fixed: every trade uses the capital above.</p>
    </Card>
  );
}

function StrategyBacktest({ s, b, historyTo, sizeLabel, viewer, period = "A", from }) {
  const [limit, setLimit] = useState(60);
  if (!b) return <Note>Working out the backtest…</Note>;
  const st = b.stats;
  const total = b.trades_total ?? b.trades.length;
  let eq = s.capital;
  const curve = b.curve?.length ? b.curve : [...b.trades].filter((r) => r.net != null).reverse().map((r) => ({ t: r.day, v: (eq += r.net) }));
  const download = async () => {
    try {
      const r = await api.get(`/option-picks/trades.csv?key=${s.key}&period=${period}`, { responseType: "blob" });
      const u = URL.createObjectURL(r.data);
      const a = document.createElement("a");
      a.href = u; a.download = `option_picks_${s.key}_backtest_${period}.csv`; a.click();
      setTimeout(() => URL.revokeObjectURL(u), 2000);
    } catch (e) { toast.error(errText(e)); }
  };
  return (
    <div className="space-y-4">
      <div className="text-[12px] text-slate-400" data-testid="opt-balance-line">
        Balance {usd0(st.final_equity ?? s.capital + st.net)} from {usd0(s.capital)} ({st.return >= 0 ? "+" : ""}{pct(st.return, 0)})
        {!viewer && st.view2_net != null && <> · with 5% worse fills and 2× fees: <span className={tone(st.view2_net)}>{signedUsd(st.view2_net, 0)}</span> · real trades at the fill price on {pct(st.real_fill, 0)} of days</>}
      </div>
      {curve.length > 1 && (
        <Card title="Account balance" sub={viewer ? `From ${usd0(s.capital)}` : `From ${usd0(s.capital)} · ${sizeLabel} · ${period === "B" ? `the server's own backtest from ${from}` : `backtest 1 Jan 2024 → ${historyTo}, then the server's own daily backtest`}`}>
          <LineChart series={[{ name: "Balance $", color: VIZ.s1, points: curve }]} xFmt={(v) => v} yFmt={(v) => n(v, 0)} unit=" $" height={150} />
        </Card>
      )}
      <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
        <Card title="Yearly" sub="Net after fees">
          <div className="overflow-x-auto scroll-thin"><table className="desk-table">
            <thead><tr><th>Year</th><th className="num">Trades</th><th className="num">Win %</th><th className="num">Gross</th><th className="num">Fees</th><th className="num">Net</th><th className="num">Balance at end</th></tr></thead>
            <tbody>{b.yearly.map((y) => (
              <tr key={y.year} className={y.year === "Total till date" ? "border-t-2 border-line2 font-semibold" : ""}>
                <td>{y.year}</td><td className="num">{y.trades}</td><td className="num">{pct(y.win_rate)}</td><td className={`num ${tone(y.gross)}`}>{signedUsd(y.gross, 0)}</td>
                <td className="num">{signedUsd(-y.fees, 0)}</td><td className={`num font-semibold ${tone(y.net)}`}>{signedUsd(y.net, 0)}</td>
                <td className="num">{y.equity != null ? usd0(y.equity) : "—"}</td>
              </tr>))}</tbody>
          </table></div>
        </Card>
        <Card title="Monthly" sub="Newest first. Losing months are red.">
          <div className="max-h-[330px] overflow-auto scroll-thin">
            <table className="desk-table">
              <thead className="sticky top-0 bg-panel2"><tr><th>Month</th><th className="num">Trades</th><th className="num">Win %</th><th className="num">Net</th><th className="num">Cumulative</th></tr></thead>
              <tbody>{[...b.monthly].reverse().map((m) => (
                <tr key={m.month} className={m.net < 0 ? "bg-red-500/10" : ""}><td className={`font-mono ${m.net < 0 ? "text-red-300" : ""}`}>{m.month}</td><td className="num">{m.trades}</td>
                  <td className="num">{pct(m.win_rate)}</td><td className={`num font-semibold ${m.net < 0 ? "text-red-400" : tone(m.net)}`}>{signedUsd(m.net, 0)}</td><td className={`num ${tone(m.cumulative)}`}>{signedUsd(m.cumulative, 0)}</td></tr>))}</tbody>
            </table>
          </div>
        </Card>
      </div>
      <Card title="Backtest trades" sub={`Newest first · ${total} trades${viewer ? "" : ` · ${sizeLabel}`}`} right={viewer ? null : <Btn tone="ghost" onClick={download}>Download all (CSV)</Btn>}>
        <div className="overflow-x-auto scroll-thin">
          <table className="desk-table">
            <thead><tr><th>Expiry</th><th>Entry (IST)</th><th className="num">BTC futures</th><th className="num">Capital</th><th className="num">Qty per leg</th><th>Legs: sell fill → exit</th><th className="num">Settlement</th><th className="num">Gross</th><th className="num">Fees</th><th className="num">Net</th><th className="num">Balance after</th><th>Real fill</th></tr></thead>
            <tbody>{b.trades.filter((r) => r.net != null).slice(0, limit).map((r) => (
              <tr key={r.day + r.entry_ist}><td className="font-mono">{r.day}</td><td className="font-mono">{r.entry_ist.slice(11)}</td><td className="num">{n(r.price, 1)}</td><td className="num">{r.capital != null ? usd0(r.capital) : "—"}</td><td className="num">{r.qty != null ? r.qty.toFixed(3) : "—"}</td>
                <td className="min-w-[300px] text-[11.5px]">{(r.legs || []).map((l) => <div key={l.sym}>{legsText([l])}</div>)}</td><td className="num">{n(r.settle, 1)}</td><td className={`num ${tone(r.gross)}`}>{signedUsd(r.gross)}</td>
                <td className="num">{signedUsd(-r.fees)}</td><td className={`num font-semibold ${tone(r.net)}`}>{signedUsd(r.net)}</td><td className="num">{r.equity != null ? usd0(r.equity) : "—"}</td><td>{r.real ? "Yes" : "No"}</td></tr>))}</tbody>
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

function Live({ d, viewer }) {
  const live = d.live || [];
  const k0 = d.enabled[0];
  return (
    <div className="space-y-4">
      <Note>Paper trades on Delta's live prices: opened at the mark at the end of the entry minute, each leg stopped on the 1-minute mark, settled at Delta's official settlement index. No orders are sent.</Note>
      <Totals s={d.live_stats[k0]} viewer={viewer} testid="opt-live-totals" />
      <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
        {d.enabled.map((k) => {
          const s = d.strategies.find((x) => x.key === k);
          const ls = d.live_stats[k] || {};
          return (
            <div key={k} className="rounded-lg border border-line bg-panel2/60 px-3 py-2.5 text-[12px]" data-testid="opt-live-status">
              <div className="font-semibold text-slate-100">{viewer ? s.name : `Pick ${s.pick} · ${s.name}`}</div>
              {!viewer && <div className="text-slate-400">Next paper entry: {d.next_entry?.[k] || "—"}</div>}
              <div className="text-slate-300">Paper: {ls.trades || 0} closed · {ls.wins || 0} won · net <span className={tone(ls.net)}>{signedUsd(ls.net || 0)}</span> · balance {usd0(ls.final_equity ?? s.capital + (ls.net || 0))} from {usd0(s.capital)}{d.compounded ? " · compounded" : ""}</div>
            </div>
          );
        })}
      </div>
      <Card title="Live paper trades" sub={viewer ? "Paper trades: no orders are sent." : `Size: ${d.size_label} · about ${usd0(d.order_notional)} sold per order`} testid="opt-live">
        <div className="overflow-x-auto scroll-thin">
          <table className="desk-table">
            <thead><tr><th>Expiry</th><th>Status</th><th>Entry (IST)</th><th className="num">BTC futures</th><th className="num">Capital</th><th className="num">Qty per leg</th><th>Legs</th><th className="num">Gross</th><th className="num">Fees</th><th className="num">Net</th><th className="num">Balance after</th>{!viewer && <th>Note</th>}</tr></thead>
            <tbody>{live.map((t) => (
              <tr key={t.id}><td className="font-mono">{t.day}</td><td className={STATUS_TONE[t.status] || ""}>{t.status}</td><td className="font-mono">{t.entry_ist ? t.entry_ist.slice(11) : "—"}</td>
                <td className="num">{n(t.price, 1)}</td><td className="num">{t.capital != null ? usd0(t.capital) : "—"}</td><td className="num">{t.qty != null ? t.qty.toFixed(3) : "—"}</td>
                <td className="min-w-[300px] text-[11.5px]">{viewer ? (t.legs || []).map((l) => <div key={l.sym}>{legsText([l])}</div>) : (t.legs || []).map((l) => <div key={l.sym}>{legLive(l)}</div>)}</td>
                <td className={`num ${tone(t.gross)}`}>{t.gross != null ? signedUsd(t.gross) : "—"}</td><td className="num">{t.fees != null ? signedUsd(-t.fees) : "—"}</td>
                <td className={`num font-semibold ${tone(t.net)}`}>{t.net != null ? signedUsd(t.net) : t.status === "OPEN" ? "open" : "—"}</td>
                <td className="num">{t.equity != null ? usd0(t.equity) : "—"}</td>{!viewer && <td className="text-[12px] text-slate-400">{t.reason || ""}</td>}</tr>))}</tbody>
          </table>
          {!live.length && <div className="p-4 text-center text-xs text-slate-500">No paper trades yet.</div>}
        </div>
      </Card>
      <Card title="Paper vs backtest" sub={viewer ? "Each paper day next to the backtest of the same day." : "Each paper day next to the server's own backtest of the same expiry (worked out about 10 minutes after the 17:30 settlement), the backtest priced at the paper trade's own size."} testid="opt-compare">
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

function JournalA({ d, s, viewer }) {
  const b = d.backtest[s.key];
  if (!b) return <Note>Working out the backtest…</Note>;
  return (
    <div className="space-y-4">
      <Note>{viewer ? "Backtest trades, not live trades. Net results are after fees."
        : `Real data: the site's own Option Picks code on Delta's mark prices, official settlement index and fee record from 1 Jan 2024 to ${dmy(d.history_to)}, then this server's own daily backtest. Backtest trades, not live trades.`}</Note>
      <Totals s={b.stats} viewer={viewer} testid="opt-a-totals" />
      <RealData rows={b.realdata} notChecked={d.not_checked} viewer={viewer} />
      <StrategyBacktest s={s} b={b} historyTo={d.history_to} sizeLabel={d.size_label} viewer={viewer} period="A" />
    </div>
  );
}

function JournalB({ d, s }) {
  const b = d.backtest[s.key]?.B, f = d.b_fill || {};
  const filling = !!(f.total && f.done < f.total);
  const progress = !filling ? null : (
    <Note tone="amber">Journal B is being filled: the server is re-running every expiry from {dmy(d.b_from)} on Delta's data, {f.done} of {f.total} days done so far
      {f.running ? " (a few minutes)" : " (it carries on at the next pass, within 15 minutes)"}{f.failed?.length ? `; not available yet: ${f.failed.map(dmy).join(", ")}` : ""}. The numbers below grow as it fills.</Note>
  );
  if (!b) return <div className="space-y-4">{progress}<Note>Working out journal B…</Note></div>;
  const m = b.match || {};
  if (!b.days) return progress || <Note>Journal B is being filled: the server is re-running every expiry from {dmy(d.b_from)} on Delta's data. This takes a few minutes after an update.</Note>;
  const ok = m.checked && m.same === m.checked;
  return (
    <div className="space-y-4">
      {progress}
      <Note>Same code on this server's Delta data from {dmy(b.from)}, starting again from {usd0(s.capital)}. Backtest trades, not live trades.</Note>
      <Totals s={b.stats} testid="opt-b-totals" />
      <div className="text-[12px] text-slate-400" data-testid="opt-b-check">
        {m.checked ? <><span className={ok ? "text-teal-300" : "text-amber-300"}>Checked against journal A: the same trade on {m.same} of {m.checked} days</span> ({dmy(b.from)} – {dmy(m.to)}){m.differ?.length ? `; differs on ${m.differ.map(dmy).join(", ")}` : ""} · </> : null}
        {b.days} expiries to {dmy(b.to)}, {b.skipped} skipped by the rules
      </div>
      <StrategyBacktest s={s} b={b} historyTo={d.history_to} sizeLabel={d.size_label} period="B" from={b.from} />
    </div>
  );
}

export default function OptionPicks() {
  const { data: d, error, reload } = useFetch("/option-picks", 30000);
  const [tab, setTab] = useState("C");
  useEffect(() => { document.title = "Option Picks"; }, []);
  if (error && !d) return <Note tone="red">Could not load Option Picks: {String(error)}</Note>;
  if (!d) return <div className="text-sm text-slate-400">Loading Option Picks…</div>;
  const btn = (on) => `rounded px-3 py-1.5 text-xs font-semibold ${on ? "bg-amber-500 text-slate-950" : "border border-line text-slate-300 hover:text-white"}`;
  const s = d.strategies[0];
  const viewer = !d.is_admin;   // users see trades and results only (the server also removes the rest from their data)
  const z = d.sizing || {};
  const same = z.put_capital === z.call_capital && z.put_leverage === z.call_leverage;
  const sub = viewer ? "Backtest journal, net after fees" : `Pick 5: short ATM BTC straddle at 17:00 IST on Delta Exchange India (expiry 17:30 IST) · ${same
    ? `$${n(z.put_capital, 0)} × ${z.put_leverage}x per leg (put + call), about $${n(z.put_capital * z.put_leverage, 0)} of BTC per leg` : d.size_label}${d.compounded ? " · compounded" : " · fixed capital"} · paper`;
  return (
    <div className="space-y-4" data-testid="option-picks-page">
      <PageHead title="Option Picks" sub={sub} />
      {!viewer && <Note>This page is paper only: no orders are sent and no alerts. The same rules run as the <Link to="/straddle-bot?bot=op" className="text-amber-300 underline">Option Picks bot</Link> (paper by default; real orders only after its Live checklist and only when you switch them on).</Note>}
      {d.error && <Note tone="red">{viewer ? "The latest update is delayed. It retries automatically." : `Last update failed: ${d.error}`}</Note>}
      {s && !viewer && (
        <div className="rounded-lg border border-line bg-panel2/60 px-3 py-2 text-[12px]">
          <div className="font-semibold text-slate-100">Pick {s.pick} · {s.name} <span className="font-mono text-[11px] font-normal text-slate-500">{s.code}</span>
            {s.recommended && <span className={`ml-2 rounded px-1.5 py-0.5 text-[11px] font-semibold ${s.recommended_active ? "bg-amber-500/20 text-amber-300" : "border border-amber-500/40 text-amber-300/80"}`} data-testid="opt-recommended">{s.recommended}{s.recommended_active ? "" : " (now set to fixed capital)"}</span>}</div>
          <div className="text-slate-300">Sell the ATM put and ATM call (same strike, nearest to BTC futures) at the close of the 17:00 IST minute, filled {s.live_entry_ist} IST, at mark − max($0.10, 1%). Skip the day if the put mark is below $20 (the +2% rise rule was removed on 27 Nov 2026). Each leg is bought back when its 1-minute mark high reaches 2× its sell price; the other leg stays open; otherwise both settle at 17:30 IST. {d.compounded ? "Size: compounded, each leg gets its share of the balance after the last closed trade." : "Size: fixed capital on every trade."}</div>
        </div>
      )}
      {d.is_admin && <Sizing d={d} reload={reload} />}
      {!viewer && (
        <div className="flex flex-wrap gap-2">
          <button className={btn(tab === "C")} onClick={() => setTab("C")} data-testid="opt-tab-C">C · Live paper trading</button>
          <button className={btn(tab === "A")} onClick={() => setTab("A")} data-testid="opt-tab-A">A · 1 Jan 2024 → today</button>
          <button className={btn(tab === "B")} onClick={() => setTab("B")} data-testid="opt-tab-B">B · {d.b_from ? new Date(`${d.b_from}T00:00:00`).toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric" }) : "1 Oct 2026"} → today</button>
        </div>
      )}
      {/* Users get journal A only (as Elite 4 Edge); live paper trading and journal B are not sent to them. */}
      {s && (viewer || tab === "A") && <JournalA d={d} s={s} viewer={viewer} />}
      {s && !viewer && tab === "B" && <JournalB d={d} s={s} />}
      {!viewer && tab === "C" && <Live d={d} viewer={viewer} />}
    </div>
  );
}
