import React, { useEffect, useState } from "react";
import { toast } from "sonner";
import { useFetch } from "@/hooks/useFetch";
import { api, errText } from "@/lib/api";
import { Card, PageHead, Note } from "@/components/desk/ui";
import { Btn } from "@/components/desk/forms";
import { n, signedUsd } from "@/lib/fmt";
import { LineChart, VIZ } from "@/components/desk/charts";

const tone = (v) => (v > 0 ? "text-teal-300" : v < 0 ? "text-red-300" : "text-slate-300");
const pct = (v) => (v == null ? "—" : `${(v * 100).toFixed(1)}%`);
const STATUS_TONE = { OPEN: "text-sky-300", CLOSED: "text-slate-200", SKIPPED: "text-slate-400", MISSED: "text-amber-300" };

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
  return (legs || []).map((l) => `${l.side} ${l.sym} @ ${n(l.fill, 2)}${l.exit ? ` → ${l.exit} ${n(l.exit_price, 2)}${l.stop_ist ? ` (${l.stop_ist})` : ""}` : ""}`).join("  ·  ");
}

function CrossCheck({ d }) {
  const rows = d.strategies;
  return (
    <Card title="Cross-check on Delta Exchange India's real data" sub="Your workbook vs the same rules rebuilt on Delta's own prices, official settlement index and fee record, including Delta's settlement fee on options that expire in the money. Picks 1 and 3 failed and are not run." testid="opt-check">
      <div className="overflow-x-auto scroll-thin">
        <table className="desk-table">
          <thead><tr><th>Pick</th><th>Strategy</th><th>Period</th><th className="num">Workbook trades</th><th className="num">Workbook net</th><th className="num">Real-data trades</th><th className="num">Real-data net</th><th className="num">Real max DD</th><th className="num">Real net 2024 → today</th><th>Verdict</th><th>On this page</th></tr></thead>
          <tbody>{rows.map((s) => {
            const c = d.check?.[s.key];
            if (!c) return null;
            return (
              <tr key={s.key} className={s.enabled ? "" : "opacity-70"}>
                <td className="font-mono">{s.pick}</td><td>{s.name}<div className="font-mono text-[11px] text-slate-500">{s.code}</div></td>
                <td className="text-[12px]">{c.workbook.period}</td>
                <td className="num">{c.workbook.trades}</td><td className={`num ${tone(c.workbook.net)}`}>{signedUsd(c.workbook.net, 0)}</td>
                <td className="num">{c.real_same_period.trades}</td><td className={`num font-semibold ${tone(c.real_same_period.net)}`}>{signedUsd(c.real_same_period.net, 0)}</td>
                <td className="num text-red-300">{signedUsd(-c.real_same_period.max_dd, 0)}</td>
                <td className={`num ${tone(c.real_full.net)}`}>{signedUsd(c.real_full.net, 0)}</td>
                <td className={`text-[12px] ${s.grade === "pass" ? "text-teal-300" : s.grade === "caution" ? "text-amber-300" : "text-red-300"}`}>{s.verdict}</td>
                <td>{s.enabled ? "Paper trading" : "Not run (failed)"}</td>
              </tr>);
          })}</tbody>
        </table>
      </div>
    </Card>
  );
}

function StrategyBacktest({ s, b, historyTo }) {
  const [limit, setLimit] = useState(60);
  if (!b) return <Note>Working out the backtest…</Note>;
  const st = b.stats;
  let eq = 0;
  const curve = [...b.trades].reverse().map((r) => ({ t: r.day, v: (eq += r.net) }));
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
        <Stat label="Trades" value={st.trades} sub={`${st.wins} won`} />
        <Stat label="Win ratio" value={pct(st.win_rate)} sub="net after fees > 0" />
        <Stat label="Gross P&L" value={signedUsd(st.gross, 0)} cls={tone(st.gross)} />
        <Stat label="Fees + GST" value={signedUsd(-st.fees, 0)} cls="text-slate-300" />
        <Stat label="Net P&L" value={signedUsd(st.net, 0)} cls={tone(st.net)} sub="$1,000 at 10x" />
        <Stat label="Max drawdown" value={signedUsd(-st.max_dd, 0)} cls="text-red-300" sub={`lowest balance $${n(st.lowest_balance, 0)}`} />
        <Stat label="Worst trade" value={signedUsd(st.worst)} cls="text-red-300" sub={`best ${signedUsd(st.best)}`} />
        <Stat label="With 5% worse fills + 2x fees" value={signedUsd(st.view2_net, 0)} cls={tone(st.view2_net)} sub={`real trades at fill on ${pct(st.real_fill)} of days`} />
      </div>
      {curve.length > 1 && (
        <Card title="Cumulative net P&L" sub={`Backtest on Delta data, 1 Jan 2024 → ${historyTo}, then the server's own daily backtest`}>
          <LineChart series={[{ name: "Net P&L $", color: VIZ.s1, points: curve }]} xFmt={(v) => v} yFmt={(v) => n(v, 0)} unit=" $" height={150} />
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
      <Card title="Backtest trades" sub={`Newest first · ${b.trades_total} trades`} right={<Btn tone="ghost" onClick={download}>Download all (CSV)</Btn>}>
        <div className="overflow-x-auto scroll-thin">
          <table className="desk-table">
            <thead><tr><th>Expiry</th><th>Entry (IST)</th><th className="num">BTC</th><th className="num">Qty</th><th>Legs: fill → exit</th><th className="num">Settlement</th><th className="num">Gross</th><th className="num">Fees</th><th className="num">Net</th><th>Real fill</th></tr></thead>
            <tbody>{b.trades.slice(0, limit).map((r) => (
              <tr key={r.day + r.entry_ist}><td className="font-mono">{r.day}</td><td className="font-mono">{r.entry_ist.slice(11)}</td><td className="num">{n(r.price, 1)}</td><td className="num">{r.qty}</td>
                <td className="text-[11.5px]">{legsText(r.legs)}</td><td className="num">{n(r.settle, 1)}</td><td className={`num ${tone(r.gross)}`}>{signedUsd(r.gross)}</td>
                <td className="num">{signedUsd(-r.fees)}</td><td className={`num font-semibold ${tone(r.net)}`}>{signedUsd(r.net)}</td><td>{r.real ? "Yes" : "No"}</td></tr>))}</tbody>
          </table>
          {b.trades.length > limit && <div className="p-2 text-center"><Btn tone="ghost" onClick={() => setLimit(limit + 60)}>Show more</Btn></div>}
        </div>
      </Card>
    </div>
  );
}

function Live({ d }) {
  const live = d.live || [];
  return (
    <div className="space-y-4">
      <div className="grid grid-cols-1 gap-2 sm:grid-cols-3">
        {d.enabled.map((k) => {
          const s = d.strategies.find((x) => x.key === k);
          const ls = d.live_stats[k] || {};
          return (
            <div key={k} className="rounded-lg border border-[#1e2a4a] bg-[#0f172a]/60 px-3 py-2.5 text-[12px]">
              <div className="font-semibold text-slate-100">Pick {s.pick} · {s.name}</div>
              <div className="text-slate-400">Next paper entry: {d.next_entry[k] || "—"}</div>
              <div className="text-slate-300">Paper: {ls.trades || 0} closed · {pct(ls.win_rate)} won · <span className={tone(ls.net)}>{signedUsd(ls.net || 0)}</span></div>
            </div>
          );
        })}
      </div>
      <Card title="Live paper trades" sub="Opened at the live Delta mark at the end of the entry bar; stops on the 1-minute mark; settled at Delta's official settlement index. No orders are sent." testid="opt-live">
        <div className="overflow-x-auto scroll-thin">
          <table className="desk-table">
            <thead><tr><th>Expiry</th><th>Pick</th><th>Status</th><th>Entry (IST)</th><th className="num">BTC</th><th className="num">Qty</th><th>Legs: mark (bid / ask) → fill → exit</th><th className="num">Net</th><th>Note</th></tr></thead>
            <tbody>{live.map((t) => {
              const s = d.strategies.find((x) => x.key === t.key);
              return (
                <tr key={t.id}><td className="font-mono">{t.day}</td><td>{s?.pick} · {t.key}</td><td className={STATUS_TONE[t.status] || ""}>{t.status}</td><td className="font-mono">{t.entry_ist ? t.entry_ist.slice(11) : "—"}</td>
                  <td className="num">{n(t.price, 1)}</td><td className="num">{t.qty ?? "—"}</td>
                  <td className="text-[11.5px]">{(t.legs || []).map((l) => `${l.side} ${l.sym} ${n(l.mark, 2)} (${l.bid ?? "—"} / ${l.ask ?? "—"}) → ${n(l.fill, 2)}${l.exit ? ` → ${l.exit} ${n(l.exit_price, 2)}` : l.stop ? ` · stop ${n(l.stop, 1)} · high ${n(l.high, 2)}` : ""}`).join("  ·  ")}</td>
                  <td className={`num font-semibold ${tone(t.net)}`}>{t.net != null ? signedUsd(t.net) : "—"}</td><td className="text-[12px] text-slate-400">{t.reason || ""}</td></tr>);
            })}</tbody>
          </table>
          {!live.length && <div className="p-4 text-center text-xs text-slate-500">No paper trades yet. The first entries are at the times shown above.</div>}
        </div>
      </Card>
      <Card title="Paper vs backtest" sub="Each paper day next to the server's own backtest of the same expiry (worked out about 10 minutes after the 17:30 settlement)." testid="opt-compare">
        <div className="overflow-x-auto scroll-thin">
          <table className="desk-table">
            <thead><tr><th>Expiry</th><th>Pick</th><th>Paper</th><th className="num">Paper net</th><th>Backtest</th><th className="num">Backtest net</th></tr></thead>
            <tbody>{d.compare.map((c) => (
              <tr key={c.day + c.key}><td className="font-mono">{c.day}</td><td>{c.key}</td><td title={c.live_reason || ""}>{c.live_status}</td><td className={`num ${tone(c.live_net)}`}>{c.live_net != null ? signedUsd(c.live_net) : "—"}</td>
                <td title={c.bt_reason || ""}>{c.bt_status}</td><td className={`num ${tone(c.bt_net)}`}>{c.bt_net != null ? signedUsd(c.bt_net) : "—"}</td></tr>))}</tbody>
          </table>
          {!d.compare.length && <div className="p-4 text-center text-xs text-slate-500">Nothing to compare yet.</div>}
        </div>
      </Card>
    </div>
  );
}

export default function OptionPicks() {
  const { data: d, error } = useFetch("/option-picks", 30000);
  const [tab, setTab] = useState("live");
  useEffect(() => { document.title = "Option Picks"; }, []);
  if (error && !d) return <Note tone="red">Could not load Option Picks: {String(error)}</Note>;
  if (!d) return <div className="text-sm text-slate-400">Loading Option Picks…</div>;
  const btn = (on) => `rounded px-3 py-1.5 text-xs font-semibold ${on ? "bg-amber-500 text-slate-950" : "border border-[#1e2a4a] text-slate-300 hover:text-white"}`;
  const enabled = d.strategies.filter((s) => s.enabled);
  const cur = enabled.find((s) => s.key === tab);
  return (
    <div className="space-y-4" data-testid="option-picks-page">
      <PageHead title="Option Picks" sub="BTC daily options on Delta Exchange India (expiry 17:30 IST) · $1,000 at 10x per strategy, about $10,000 of BTC per leg · paper only" />
      <Note>Paper only: no orders are sent and no alerts. Each strategy is tracked on its own; they are not added together.</Note>
      {d.error && <Note tone="red">Last update failed: {d.error}</Note>}
      <CrossCheck d={d} />
      <div className="grid grid-cols-1 gap-2 lg:grid-cols-3">
        {enabled.map((s) => (
          <div key={s.key} className="rounded-lg border border-[#1e2a4a] bg-[#0f172a]/60 px-3 py-2 text-[12px]">
            <div className="font-semibold text-slate-100">Pick {s.pick} · {s.name}</div>
            <div className="font-mono text-[11px] text-slate-500">{s.code}</div>
            <div className="text-slate-300">Entry {s.entry_ist} bar, filled {s.live_entry_ist} IST · stop: {s.stop}</div>
          </div>
        ))}
      </div>
      <div className="flex flex-wrap gap-2">
        <button className={btn(tab === "live")} onClick={() => setTab("live")} data-testid="opt-tab-live">Live paper trading</button>
        {enabled.map((s) => <button key={s.key} className={btn(tab === s.key)} onClick={() => setTab(s.key)} data-testid={`opt-tab-${s.key}`}>Backtest · pick {s.pick} {s.key}</button>)}
      </div>
      {tab === "live" ? <Live d={d} /> : cur && <StrategyBacktest s={cur} b={d.backtest[cur.key]} historyTo={d.history_to} />}
    </div>
  );
}
