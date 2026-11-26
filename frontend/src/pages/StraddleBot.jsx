import React, { useEffect, useMemo, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { toast } from "sonner";
import { api, errText } from "@/lib/api";
import { useFetch } from "@/hooks/useFetch";
import { Card, PageHead, Note, KpiStrip, Empty, Tabs, Chip } from "@/components/desk/ui";
import { Field, Input, Select, Btn } from "@/components/desk/forms";
import { LineChart, VIZ } from "@/components/desk/charts";
import { n, signedUsd, ist } from "@/lib/fmt";

const tone = (v) => (v > 0 ? "text-teal-300" : v < 0 ? "text-red-300" : "text-slate-200");
const usd = (v, d = 2) => (v == null ? "—" : `${v < 0 ? "−" : ""}$${n(Math.abs(v), d)}`);
const pc = (v, d = 1) => (v == null ? "—" : `${n(v * 100, d)}%`);
const num = (v, d = 2) => (v == null ? "—" : n(v, d));
const MODE_NAME = { PAPER: "Paper", TESTNET: "Testnet", LIVE: "Live" };
const SHORT = { mark_fixed: "Mark · fixed", mark_compound: "Mark · compounded", trade_fixed: "Traded · fixed", trade_compound: "Traded · compounded",
  op_compound: "Compounded ★ Recommended", op_fixed: "Fixed capital" };
const variantsOf = (bt) => (bt?.order || Object.keys(bt?.variants || {})).map((k) => [k, SHORT[k] || bt.variants[k].name]);
const FIRST = { sb: "mark_fixed", op: "op_compound" };
const BOT_SUB = {
  sb: "Sell the ATM BTC call + put on Delta India at 17:00 IST, stop at 2 × premium, otherwise hold to the 17:30 expiry. Admin only.",
  op: "Option Picks pick 5 rules: sell the ATM BTC call + put at 17:01 IST (strike from BTC futures), each leg stopped on its own at 2 × its sell price, compounded capital. Admin only.",
};
// what a setting means for the Option Picks bot, where it differs
const OP_LABEL = {
  leverage: ["Leverage (× each leg's capital)", "Each leg gets half the equity; notional of a leg = half the equity × this"],
  stop_multiple: ["Stop multiple (× each leg's sell price)", "Each leg is bought back on its own; the other leg stays open"],
  stop_mode: ["Stop check", "tick = every price update, like the backtest's 1-minute mark high · parity = only at each 1-minute close"],
  disaster_multiple: ["Backup stop on the exchange (× each leg's sell price)", "Each leg, in case this server stops"],
};

// Summary sheet rows: [label, key, formatter]
const METRICS = [
  ["Trading days", "days", (v) => num(v, 0)], ["Trades taken", "trades", (v) => num(v, 0)], ["Winning trades", "wins", (v) => num(v, 0)], ["Losing trades", "losses", (v) => num(v, 0)],
  ["Win ratio", "win_ratio", pc], ["Exits at expiry", "expiry_exits", (v) => num(v, 0)], ["Exits by stop-loss", "stop_exits", (v) => num(v, 0)], ["Days skipped", "skipped", (v) => num(v, 0)],
  ["Premium collected", "premium", usd], ["Gross P&L", "gross", signedUsd], ["Fees incl. GST", "fees", usd], ["Net P&L", "net", signedUsd], ["Final equity", "final_equity", usd],
  ["Total return", "total_return", (v) => pc(v, 1)], ["CAGR", "cagr", (v) => pc(v, 1)], ["Max drawdown ($)", "max_dd", usd], ["Max drawdown (%)", "max_dd_pct", (v) => pc(v, 1)],
  ["Worst trade ($)", "worst", signedUsd], ["Worst trade (% of equity)", "worst_pct", (v) => pc(v, 2)], ["Best trade ($)", "best", signedUsd], ["Best trade (% of equity)", "best_pct", (v) => pc(v, 2)],
  ["Average win", "avg_win", signedUsd], ["Average loss", "avg_loss", signedUsd], ["Profit factor", "profit_factor", num], ["Average P&L per trade", "avg_net", signedUsd],
  ["Average daily return", "avg_daily_return", (v) => pc(v, 3)], ["Std. dev. of daily return", "std", (v) => pc(v, 3)], ["Sharpe (√365)", "sharpe", num],
  ["Longest losing streak", "longest_streak", (v) => num(v, 0)], ["Average P ($/BTC)", "avg_p", num], ["Average lots per leg", "avg_lots", (v) => num(v, 0)],
  ["Maximum lots per leg", "max_lots", (v) => num(v, 0)], ["Maximum notional per leg", "max_notional", (v) => usd(v, 0)], ["Buy-back ÷ P on stop days", "avg_stop_over_p", num],
  ["Risk : reward (1 : x)", "risk_reward", num], ["Break-even win ratio", "breakeven_win_ratio", pc],
];

const SETTINGS = [
  ["Size", [["starting_capital", "Starting capital ($)"], ["leverage", "Leverage (× equity per leg)", "Notional of each leg = this × equity"], ["sizing_mode", "Sizing", "fixed = starting capital every day · compounded = current equity"],
    ["max_lots", "Maximum lots per leg"], ["live_small_size", "Live: start small (0 or 1)", "1 = Live mode uses leverage 5 at most"],
    ["exchange_leverage", "Order leverage on Delta (×)", "Sets the margin Delta blocks. The bot lowers it by itself when premiums are high, so the backup stop comes before a liquidation"]]],
  ["Stop", [["stop_multiple", "Stop multiple (× P)"], ["stop_mode", "Stop check", "parity = at each 1-minute close, like the backtest · tick = every price update"],
    ["disaster_multiple", "Backup stop on the exchange (× P)", "Each leg, in case this server stops"]]],
  ["Slippage", [["max_entry_slip", "Max entry slippage (%)", "Never sells below mark minus this"], ["max_exit_slip", "Max stop-exit slippage (%)", "First 5 seconds; market orders after"],
    ["entry_deadline_sec", "Entry deadline (seconds)"], ["max_spread_pct", "Skip if bid-ask spread above (% of mark)"], ["max_stale_sec", "Skip if prices older than (seconds)"]]],
  ["Risk", [["max_daily_loss_pct", "Daily max loss (% of equity)"], ["max_dd_pct", "Halt at drawdown from peak (%)"], ["max_losing_streak", "Halt after losing streak above"],
    ["slip_halt_days", "Halt after high-slippage days in a row"]]],
  ["Other", [["gst", "GST on fees (%)"], ["usd_inr", "USD/INR for an INR wallet"], ["alerts", "Alerts (0 or 1)", "Email, push and Telegram if set up"]]],
];

function useCountdown(to) {
  const [, tick] = useState(0);
  useEffect(() => { const i = setInterval(() => tick((x) => x + 1), 1000); return () => clearInterval(i); }, []);
  const s = Math.max(0, Math.round((Date.parse(to) - Date.now()) / 1000));
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60);
  return `${h ? `${h}h ` : ""}${String(m).padStart(2, "0")}m ${String(s % 60).padStart(2, "0")}s`;
}

function Fig({ label, value, sub, cls = "" }) {
  return (
    <div className="rounded-sm border border-line bg-panel2 px-3 py-2">
      <div className="text-[11px] text-slate-400">{label}</div>
      <div className={`font-mono text-[17px] font-semibold ${cls || "text-slate-50"}`}>{value}</div>
      {sub && <div className="text-[11px] text-slate-500">{sub}</div>}
    </div>
  );
}

function LiveTrade({ d }) {
  const v = d.view || {}, t = d.today, cd = useCountdown(v.expiry || d.next_entry);
  if (v.phase === "OPEN" && v.legs) {
    return (
      <Card title={`Open trade: short ${n(v.strike, 0)} straddle, each leg on its own`} sub={`${v.call} + ${v.put} · ${n(v.lots, 0)} lots a leg · stop check: ${v.stop_mode === "tick" ? "every price update" : "each 1-minute close"}`} testid="straddle-open">
        <div className="mb-2 grid grid-cols-1 gap-2 sm:grid-cols-2">
          {["call", "put"].map((k) => {
            const l = v.legs[k], near = l.distance_pct != null && l.distance_pct < 25;
            return (
              <div key={k} className="rounded-sm border border-line bg-panel2 px-3 py-2 text-[12px]" data-testid={`straddle-leg-${k}`}>
                <div className="font-semibold capitalize text-slate-100">{k} {l.exit === "Stop" ? <span className="ml-1 text-amber-300">stopped at {num(l.exit_price)}</span> : <span className="ml-1 text-slate-400">{n(l.open_lots, 0)} lots open</span>}</div>
                <div className="font-mono text-slate-300">sold {num(l.entry)} · stop {num(l.stop)} · mark {num(l.mark)}</div>
                {l.exit !== "Stop" && <div className={near ? "text-amber-300" : "text-teal-300"}>{l.distance_pct == null ? "—" : `${n(l.distance_pct, 1)}% room before its stop`}</div>}
              </div>
            );
          })}
        </div>
        <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
          <Fig label="Entry premium P" value={num(v.p)} sub="$ per BTC, call + put" />
          <Fig label="P&L now" value={signedUsd(v.unrealised)} cls={tone(v.unrealised)} sub="stopped leg + open leg, before fees" />
          <Fig label="Time to expiry" value={cd} sub="17:30 IST" />
          <Fig label="Margin blocked" value={usd(v.margin_used)} sub={v.feed_ok ? "price feed connected" : "price feed reconnecting"} cls={v.feed_ok ? "" : "text-amber-300"} />
        </div>
      </Card>
    );
  }
  if (v.phase === "OPEN") {
    const near = v.distance_pct != null && v.distance_pct < 25;
    return (
      <Card title={`Open trade: short ${n(v.strike, 0)} straddle`} sub={`${v.call} + ${v.put} · ${n(v.lots, 0)} lots a leg · stop check: ${v.stop_mode === "tick" ? "every price update" : "each 1-minute close"}`} testid="straddle-open">
        <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
          <Fig label="Entry premium P" value={num(v.p)} sub="$ per BTC, call + put" />
          <Fig label="Stop level" value={num(v.stop_level)} sub="buy both back at or above" />
          <Fig label="Call + put now" value={num(v.combined)} sub="mark price" cls={near ? "text-amber-300" : ""} />
          <Fig label="Distance to stop" value={v.distance_pct == null ? "—" : `${n(v.distance_pct, 1)}%`} cls={near ? "text-amber-300" : "text-teal-300"} sub="room before the stop" />
          <Fig label="Unrealised P&L" value={signedUsd(v.unrealised)} cls={tone(v.unrealised)} sub="before fees" />
          <Fig label="Break-evens" value={`${n(v.be_low, 0)} – ${n(v.be_high, 0)}`} sub={`BTC index now ${n(v.spot, 1)}`} />
          <Fig label="Time to expiry" value={cd} sub="17:30 IST" />
          <Fig label="Margin blocked" value={usd(v.margin_used)} sub={v.feed_ok ? "price feed connected" : "price feed reconnecting"} cls={v.feed_ok ? "" : "text-amber-300"} />
        </div>
      </Card>
    );
  }
  const fut = v.futures ? ` BTC futures ${n(v.futures, 1)}${v.fut_1731 ? ` (${n((v.futures / v.fut_1731 - 1) * 100, 2)}% since 17:31 IST yesterday)` : ""}.` : "";
  const msg = v.phase === "PREFLIGHT" ? `Pre-flight done. Entry in ${cd}. BTC index ${n(v.spot, 1)}.${fut} Strikes watched: ${(v.strikes || []).map((k) => n(k, 0)).join(", ")}.`
    : v.phase === "SETTLING" ? "Expired. Waiting for Delta's settlement price."
    : t?.status === "CLOSED" ? `Today's trade is closed: ${t.exit_reason === "Stop" ? `stop exit at ${t.exit_time} IST${t.exit_note ? ` (${t.exit_note})` : ""}`
      : t.exit_reason === "Stop+Expiry" ? `one leg stopped (${t.call_exit === "Stop" ? `call at ${t.call_stop_time}` : `put at ${t.put_stop_time}`} IST), the other held to expiry` : "held to expiry"}.`
    : t?.status === "SKIPPED" ? `Today was skipped: ${t.skip_reason}.`
    : `No open trade. Next entry in ${cd} (${(d.entry_ist || "17:00").slice(0, 5)} IST).`;
  return <Card title="Live trade" testid="straddle-idle"><p className="text-[13px] text-slate-200">{msg}</p></Card>;
}

function Controls({ d, act }) {
  const [liveBox, setLiveBox] = useState(false), [word, setWord] = useState(""), [killAsk, setKillAsk] = useState(false);
  const r = d.ready, q = `?bot=${d.bot || "sb"}`;
  return (
    <Card title="Mode and controls" sub="Paper = simulated fills at the real bid/ask, no orders sent. Testnet = Delta's practice exchange. Live = real orders, real money." testid="straddle-controls">
      <div className="flex flex-wrap items-center gap-2">
        {["PAPER", "TESTNET"].map((m) => <Btn key={m} tone={d.mode === m ? "gold" : "ghost"} onClick={() => d.mode !== m && act(() => api.post(`/straddle/mode${q}`, { mode: m }), `Mode is now ${MODE_NAME[m]}`)} data-testid={`straddle-mode-${m}`}>{MODE_NAME[m]}</Btn>)}
        <Btn tone={d.mode === "LIVE" ? "red" : "ghost"} onClick={() => d.mode !== "LIVE" && setLiveBox(!liveBox)} data-testid="straddle-mode-LIVE">Live</Btn>
        <span className="mx-1 h-5 w-px bg-line" />
        {d.enabled
          ? <Btn tone="ghost" onClick={() => act(() => api.post(`/straddle/control${q}`, { action: "disable" }), "Bot switched off")} data-testid="straddle-disable">Switch bot off</Btn>
          : <Btn onClick={() => act(() => api.post(`/straddle/control${q}`, { action: "enable" }), "Bot switched on")} data-testid="straddle-enable">Switch bot on</Btn>}
        {d.halted && <Btn onClick={() => act(() => api.post(`/straddle/control${q}`, { action: "clear_halt" }), "Halt cleared")} data-testid="straddle-clear-halt">Re-enable trading</Btn>}
        {killAsk
          ? <><Btn tone="red" onClick={() => act(() => api.post(`/straddle/control${q}`, { action: "kill" }), "Flat. Bot is off.").then(() => setKillAsk(false))} data-testid="straddle-kill-confirm">Yes, flatten now</Btn><Btn tone="ghost" onClick={() => setKillAsk(false)}>Cancel</Btn></>
          : <Btn tone="red" onClick={() => setKillAsk(true)} data-testid="straddle-kill">Kill switch</Btn>}
      </div>
      <p className="mt-2 text-[11.5px] text-slate-400">Kill switch: cancels this bot's orders, buys back its open legs at once and switches it off. The other bot and other strategies on the same Delta account are not touched.</p>
      {r.other_on_exchange && <p className="mt-1 text-[11.5px] text-amber-300">{r.other_on_exchange}: this bot can only use Paper until that one is back on Paper (both sell the same contracts on the same account).</p>}
      {liveBox && d.mode !== "LIVE" && (
        <div className="mt-3 rounded-sm border border-red-500/40 bg-red-500/5 p-3" data-testid="straddle-live-box">
          <ul className="mb-2 space-y-0.5 text-[12px] text-slate-300">
            <li>1. This bot ran on Paper for {r.paper_days_needed} trading days: {r.paper_days >= r.paper_days_needed ? "yes" : `no (${r.paper_days} so far)`}</li>
            <li>2. Live API key saved (Auto-Trade page, trading permission only, no withdrawal, IP whitelisted): {r.live_key ? "yes" : "no"}</li>
            <li>3. Live connection test passed (Rehearsal, below): {r.conn_ok_LIVE ? `yes, ${ist(r.conn_ok_LIVE)}` : "no"}</li>
            <li>4. Testnet round trip passed (Rehearsal, below): {r.drill_ok_TESTNET ? `yes, ${ist(r.drill_ok_TESTNET)}` : r.drill_waived ? "waived: Testnet had no usable market" : "no"}</li>
            <li>5. Delta's own automation, manual trades and the other bot are off the daily BTC options{r.other_on_exchange ? ` (now: ${r.other_on_exchange})` : ""}. The bot skips the day if it finds another position there.</li>
            <li>6. Live starts at leverage 5 at most until you set "Live: start small" to 0. For the first live days, also set Maximum lots to 1.</li>
          </ul>
          <div className="flex flex-wrap items-end gap-2">
            <Field label="Type LIVE to place real orders" hint="Real money."><Input value={word} onChange={(e) => setWord(e.target.value)} data-testid="straddle-live-word" /></Field>
            <Btn tone="red" disabled={word.trim() !== "LIVE"} onClick={() => act(() => api.post(`/straddle/mode${q}`, { mode: "LIVE", confirm: word }), "Mode is now Live").then(() => { setLiveBox(false); setWord(""); })} data-testid="straddle-live-confirm">Switch to Live</Btn>
          </div>
        </div>
      )}
    </Card>
  );
}

function Rehearsal({ d, setPushed }) {
  const [busy, setBusy] = useState(null);
  const r = d.ready;
  const go = async (kind, mode, label) => {
    setBusy(label);
    try {
      const res = await api.post(`/straddle/check?bot=${d.bot || "sb"}`, { kind, mode }, { timeout: 120000 });
      setPushed(res.data.dashboard);
      (res.data.check.ok ? toast.success : toast.error)(res.data.check.ok ? `${label}: passed` : `${label}: failed. See the steps below.`);
    } catch (e) { toast.error(errText(e)); } finally { setBusy(null); }
  };
  const off = !!busy || r.quiet;
  return (
    <Card title="Rehearsal" sub="Run these before real money. They use the bot's own code on Delta's real API, outside the 17:00 schedule." testid="straddle-rehearsal">
      <div className="flex flex-wrap items-center gap-2">
        <Btn tone="ghost" disabled={off || !r.testnet_key} onClick={() => go("connection", "TESTNET", "Testnet connection test")} data-testid="straddle-check-conn-testnet">Testnet connection test</Btn>
        <Btn tone="ghost" disabled={off || !r.testnet_key} onClick={() => go("round_trip", "TESTNET", "Testnet round trip")} data-testid="straddle-check-drill">Testnet round trip (1 lot)</Btn>
        <Btn tone="ghost" disabled={off || !r.live_key} onClick={() => go("connection", "LIVE", "Live connection test")} data-testid="straddle-check-conn-live">Live connection test (no trade)</Btn>
        {busy && <span className="text-[12px] text-amber-300">{busy} is running, up to a minute…</span>}
      </div>
      <ul className="mt-2 space-y-0.5 text-[11.5px] text-slate-400">
        <li><b className="text-slate-300">Connection test:</b> key, clock, wallet, today's contracts, prices, order leverage, and one order far above the market that is cancelled at once. It cannot fill.</li>
        <li><b className="text-slate-300">Testnet round trip:</b> on Delta's practice exchange, sells 1 lot of each leg, checks the backup stops and the margin, {d.bot === "op" ? "buys the call back on its own (the per-leg stop path), then the put" : "buys both back"}, checks nothing is left.</li>
        {r.quiet && <li className="text-amber-300">Not available between 16:45 and 17:40 IST: the bot is trading then.</li>}
        {!r.testnet_key && <li className="text-amber-300">No Testnet key is saved (Auto-Trade page).</li>}
        {!r.live_key && <li>No Live key is saved yet (Auto-Trade page). The Live test needs it.</li>}
      </ul>
      {d.checks.map((c, i) => (
        <div key={i} className="mt-3 rounded-sm border border-line" data-testid="straddle-check-result">
          <div className="flex flex-wrap items-center justify-between gap-2 border-b border-line bg-panel2 px-2 py-1 text-[12px]">
            <span className="text-slate-200">{c.kind === "connection" ? "Connection test" : "Round trip"} · {MODE_NAME[c.mode]} · {ist(c.at)}</span>
            <Chip status={c.ok ? "PASS" : c.waived ? "WARN" : "FAIL"} label={c.ok ? "PASSED" : c.waived ? "NO TESTNET MARKET" : "FAILED"} />
          </div>
          <table className="desk-table"><tbody>
            {c.steps.map((x, j) => (
              <tr key={j}><td className={`whitespace-nowrap font-semibold ${x.ok ? "text-teal-300" : "text-red-300"}`}>{x.ok ? "OK" : "FAILED"}</td>
                <td className="whitespace-nowrap text-slate-200">{x.name}</td><td className="text-slate-300">{x.detail}</td><td className="num text-slate-500">{n(x.ms, 0)} ms</td></tr>
            ))}
          </tbody></table>
        </div>
      ))}
    </Card>
  );
}

function Compare({ bt, live, mode, variant, setVariant, op }) {
  const b = bt.variants[variant] || Object.values(bt.variants)[0], lm = live.metrics;
  const btCurve = useMemo(() => b.curve.map((p) => ({ t: p.t, v: p.equity })), [b]);
  const btDd = useMemo(() => b.curve.map((p) => ({ t: p.t, v: p.dd_pct })), [b]);
  const day = (t) => t;
  return (
    <>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <Tabs items={variantsOf(bt)} value={variant} onChange={setVariant} testid="straddle-variant" />
        <span className="text-[11.5px] text-slate-500">Backtest: {bt.source}</span>
      </div>
      <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
        <Card title={`Backtest equity: ${b.name}`} sub={`$${n(bt.inputs.starting_capital, 0)} start, leverage ${n(bt.inputs.leverage, 0)}×${op ? " on each leg's half" : ""}`} testid="straddle-bt-equity">
          <LineChart series={[{ name: "Equity", color: VIZ.s1, points: btCurve }]} yFmt={(v) => `$${n(v, 0)}`} xFmt={day} height={190} testid="straddle-bt-equity-chart" />
          <div className="mt-3 text-[12px] font-semibold text-slate-300">Drawdown from peak (%)</div>
          <LineChart series={[{ name: "Drawdown", color: VIZ.down, points: btDd }]} yFmt={(v) => `${n(v, 1)}%`} xFmt={day} height={120} testid="straddle-bt-dd-chart" />
        </Card>
        <Card title={`${MODE_NAME[mode]} equity`} sub="The bot's own trades in the current mode" testid="straddle-live-equity">
          {live.curve.length >= 2 ? <>
            <LineChart series={[{ name: "Equity", color: VIZ.s3, points: live.curve.map((p) => ({ t: p.t, v: p.equity })) }]} yFmt={(v) => `$${n(v, 0)}`} xFmt={day} height={190} testid="straddle-live-equity-chart" />
            <div className="mt-3 text-[12px] font-semibold text-slate-300">Drawdown from peak (%)</div>
            <LineChart series={[{ name: "Drawdown", color: VIZ.down, points: live.curve.map((p) => ({ t: p.t, v: p.dd_pct })) }]} yFmt={(v) => `${n(v, 1)}%`} xFmt={day} height={120} testid="straddle-live-dd-chart" />
          </> : <Empty>The chart appears after two trading days in {MODE_NAME[mode]} mode.</Empty>}
        </Card>
      </div>
      <Card title="Backtest vs the bot, same formulas" sub={op ? "Same formulas as the Option Picks journal (taker fee on entry and exit, settlement fee on the in-the-money leg)." : "Every row uses the formula of your Summary sheet."} testid="straddle-compare" pad={false}>
        <div className="overflow-x-auto scroll-thin">
          <table className="desk-table" data-testid="straddle-compare-table">
            <thead><tr><th>Metric</th><th className="num">Backtest ({b.name})</th><th className="num">{MODE_NAME[mode]} (the bot)</th></tr></thead>
            <tbody>
              {METRICS.map(([label, key, f]) => (
                <tr key={key}><td>{op && key === "stop_exits" ? "Legs stopped" : op && key === "avg_stop_over_p" ? "Buy-back ÷ P when both legs stopped" : label}</td><td className="num">{b.metrics[key] == null && key === "skipped" ? "0" : f(b.metrics[key])}</td><td className="num">{lm.trades || key === "days" || key === "skipped" ? f(lm[key]) : "—"}</td></tr>
              ))}
              <tr><td>Average entry slippage vs mark</td><td className="num">0% (assumed)</td><td className="num">{live.avg_entry_slip == null ? "—" : pc(live.avg_entry_slip, 2)}</td></tr>
              <tr><td>Average stop-exit slippage vs mark</td><td className="num">0% (assumed)</td><td className="num">{live.avg_exit_slip == null ? "—" : pc(live.avg_exit_slip, 2)}</td></tr>
              <tr><td>Average order latency</td><td className="num">—</td><td className="num">{live.avg_latency_ms == null ? "—" : `${n(live.avg_latency_ms, 0)} ms`}</td></tr>
            </tbody>
          </table>
        </div>
      </Card>
    </>
  );
}

function Monthly({ bt, live, mode, variant, setVariant, op }) {
  const b = bt.variants[variant] || Object.values(bt.variants)[0];
  const by = Object.fromEntries(live.monthly.map((m) => [m.month, m]));
  const months = [...new Set([...b.monthly.map((m) => m.month), ...live.monthly.map((m) => m.month)])].sort().reverse();
  const bb = Object.fromEntries(b.monthly.map((m) => [m.month, m]));
  const cells = (m) => (m ? <><td className="num">{usd(m.before)}</td><td className={`num ${tone(m.net)}`}>{signedUsd(m.net)}</td><td className="num">{usd(m.after)}</td><td className="num">{pc(m.win_ratio, 0)}</td><td className="num">{m.stops}</td></>
    : <><td className="num">—</td><td className="num">—</td><td className="num">—</td><td className="num">—</td><td className="num">—</td></>);
  return (
    <>
      <Tabs items={variantsOf(bt)} value={variant} onChange={setVariant} testid="straddle-variant-m" />
      <Card title="Monthly" sub={`Left: backtest (${b.name}). Right: the bot in ${MODE_NAME[mode]} mode.`} testid="straddle-monthly" pad={false}>
        <div className="overflow-x-auto scroll-thin">
          <table className="desk-table">
            <thead>
              <tr><th /><th colSpan={5} className="text-center">Backtest</th><th colSpan={5} className="text-center">{MODE_NAME[mode]}</th></tr>
              <tr><th>Month</th>{[0, 1].map((k) => <React.Fragment key={k}><th className="num">Capital before</th><th className="num">Net P&L</th><th className="num">Capital after</th><th className="num">Win ratio</th><th className="num">{op ? "Legs stopped" : "Stops"}</th></React.Fragment>)}</tr>
            </thead>
            <tbody>{months.map((m) => <tr key={m}><td className="font-mono">{m}</td>{cells(bb[m])}{cells(by[m])}</tr>)}</tbody>
          </table>
        </div>
      </Card>
    </>
  );
}

const MONEY = new Set(["premium", "cost", "gross", "entry_fees", "exit_fees", "fees", "net", "equity", "peak", "dd", "sizing_equity", "notional", "leg_risk_cost"]);
const PCT = new Set(["net_pct", "dd_pct", "entry_slip", "exit_slip"]);
const RATE = new Set(["maker", "taker", "cap"]);
function cell(k, v) {
  if (v == null || v === "") return "—";
  if (Array.isArray(v)) return v.join(", ");
  if (typeof v === "boolean") return v ? "Yes" : "No";
  if (typeof v !== "number") return String(v);
  if (MONEY.has(k)) return k === "net" || k === "gross" || k === "dd" ? signedUsd(v) : usd(v);
  if (PCT.has(k)) return pc(v, 2);
  if (RATE.has(k)) return `${n(v * 100, 3)}%`;
  if (k === "lots" || k === "streak" || k === "latency_ms") return n(v, 0);
  if (k === "position") return n(v, 3);
  return n(v, 2);
}

function Journal({ mode, bot, bt }) {
  const [src, setSrc] = useState(FIRST[bot]), [page, setPage] = useState(null), [limit, setLimit] = useState(100);
  useEffect(() => {
    let on = true;
    api.get(`/straddle/journal?bot=${bot}&src=${src}&limit=${limit}`).then((r) => on && setPage(r.data)).catch((e) => toast.error(errText(e)));
    return () => { on = false; };
  }, [src, limit, bot]);
  const csv = async () => {
    try {
      const r = await api.get(`/straddle/journal.csv?bot=${bot}&src=${src}`, { responseType: "blob" });
      const u = URL.createObjectURL(r.data), a = document.createElement("a");
      a.href = u; a.download = `${bot === "op" ? "option_picks_bot" : "straddle"}_${src.toLowerCase()}_journal.csv`; a.click(); URL.revokeObjectURL(u);
    } catch (e) { toast.error(errText(e)); }
  };
  const items = [...(bt ? variantsOf(bt) : []).map(([k, l]) => [k, `Backtest: ${l}`]), [mode, `${MODE_NAME[mode]} journal (the bot)`]];
  return (
    <>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <Tabs items={items} value={src} onChange={(k) => { setPage(null); setLimit(100); setSrc(k); }} testid="straddle-journal-src" />
        <Btn tone="ghost" onClick={csv} data-testid="straddle-journal-csv">Download CSV (all rows)</Btn>
      </div>
      <Card title="Trade journal" sub="Newest first. Same columns as your workbook; the bot's journal adds order IDs, fill times, latency, slippage and flags." testid="straddle-journal" pad={false}>
        {!page ? <div className="p-3"><Empty>Loading…</Empty></div> : !page.rows.length ? <div className="p-3"><Empty>No trades yet in this journal. The first entry is at {bot === "op" ? "17:01" : "17:00"} IST.</Empty></div> : (
          <>
            <div className="overflow-x-auto scroll-thin">
              <table className="desk-table" data-testid="straddle-journal-table">
                <thead><tr>{page.columns.map((c) => <th key={c.key} className={typeof page.rows[0][c.key] === "number" ? "num" : ""}>{c.label}</th>)}</tr></thead>
                <tbody>
                  {page.rows.map((r) => (
                    <tr key={r.date}>{page.columns.map((c) => (
                      <td key={c.key} className={`${typeof r[c.key] === "number" ? "num" : "whitespace-nowrap"} ${c.key === "net" ? tone(r.net) : c.key === "result" ? (r.result === "Win" ? "text-teal-300" : r.result === "Loss" ? "text-red-300" : "text-slate-400") : ""}`}>{cell(c.key, r[c.key])}</td>
                    ))}</tr>
                  ))}
                </tbody>
              </table>
            </div>
            <div className="flex items-center justify-between border-t border-line px-2 py-1.5 font-mono text-[11px] text-slate-500">
              <span>Showing {n(page.rows.length, 0)} of {n(page.total, 0)} rows</span>
              {page.total > page.rows.length && <button type="button" className="text-amber-400 hover:text-amber-300" onClick={() => setLimit(1000)}>Show all</button>}
            </div>
          </>
        )}
      </Card>
    </>
  );
}

function SettingsTab({ d, act, onImported }) {
  const [v, setV] = useState(d.settings), file = useRef(null), [busy, setBusy] = useState(false);
  useEffect(() => { setV(d.settings); }, [d.settings]);
  const op = d.bot === "op";
  const dirty = Object.keys(v).filter((k) => !k.startsWith("_") && String(v[k]) !== String(d.settings[k]));
  const save = () => act(() => api.post(`/straddle/settings?bot=${d.bot || "sb"}`, { patch: Object.fromEntries(dirty.map((k) => [k, d.choices[k] ? v[k] : Number(v[k])])) }), "Settings saved");
  const upload = async (e) => {
    const f = e.target.files?.[0];
    if (!f) return;
    setBusy(true);
    try {
      const r = await api.post("/straddle/backtest/import", f, { headers: { "Content-Type": "application/octet-stream", "X-Filename": f.name } });
      onImported(r.data.backtest);
      toast.success(`Imported ${r.data.days} days`);
    } catch (err) { toast.error(errText(err)); } finally { setBusy(false); e.target.value = ""; }
  };
  return (
    <>
      <Card title="Settings" sub="Stored on the server. Every change is logged below. A change applies from the next trade." testid="straddle-settings"
        right={<Btn disabled={!dirty.length} onClick={save} data-testid="straddle-settings-save">{dirty.length ? `Save ${dirty.length} change${dirty.length > 1 ? "s" : ""}` : "Saved"}</Btn>}>
        {SETTINGS.map(([group, rows]) => (
          <div key={group} className="mb-4 last:mb-0">
            <div className="mb-2 text-[12px] font-semibold text-slate-200">{group}</div>
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
              {rows.map(([k, label0, hint0]) => {
                const [label, hint] = op && OP_LABEL[k] ? OP_LABEL[k] : [label0, hint0];
                return (
                <Field key={k} label={label} hint={hint || (d.bounds[k] ? `${d.bounds[k][0]} to ${n(d.bounds[k][1], 0)} · default ${d.defaults[k]}` : `default ${d.defaults[k]}`)}>
                  {d.choices[k]
                    ? <Select value={v[k]} onChange={(e) => setV({ ...v, [k]: e.target.value })} options={d.choices[k]} data-testid={`straddle-set-${k}`} />
                    : <Input type="number" value={v[k]} onChange={(e) => setV({ ...v, [k]: e.target.value })} data-testid={`straddle-set-${k}`} />}
                </Field>
                );
              })}
            </div>
          </div>
        ))}
      </Card>
      {op ? (
        <Card title="Backtest" sub="The Option Picks bot's backtest is Option Picks pick 5's own data (Delta's real prices from 1 Jan 2024, then the server's daily backtest), re-sized at the starting capital and leverage above." testid="straddle-import" />
      ) : (
        <Card title="Backtest workbook" sub="Import a newer version of your Excel backtest (sheets J_Mark_Fixed and J_Trade_Fixed). The numbers are re-computed from its day-by-day prices." testid="straddle-import">
          <input ref={file} type="file" accept=".xlsx" className="hidden" onChange={upload} data-testid="straddle-import-file" />
          <Btn tone="ghost" disabled={busy} onClick={() => file.current?.click()} data-testid="straddle-import-btn">{busy ? "Importing…" : "Import Excel backtest"}</Btn>
        </Card>
      )}
      <Card title="Change log" testid="straddle-changes" pad={false}>
        {d.changes.length ? (
          <div className="overflow-x-auto scroll-thin"><table className="desk-table"><thead><tr><th>When</th><th>Setting</th><th className="num">From</th><th className="num">To</th><th>By</th></tr></thead>
            <tbody>{d.changes.map((c, i) => <tr key={i}><td className="font-mono">{ist(c.at)}</td><td>{c.key}</td><td className="num">{String(c.old)}</td><td className="num">{String(c.new)}</td><td>{c.by}</td></tr>)}</tbody></table></div>
        ) : <div className="p-3"><Empty>No setting has been changed yet.</Empty></div>}
      </Card>
    </>
  );
}

function BotSwitch({ bot, setBot, bots }) {
  const btn = (on) => `rounded-sm border px-3 py-1.5 text-left text-[12.5px] ${on ? "border-amber-500 bg-amber-500/10 text-amber-200" : "border-line text-slate-300 hover:text-white"}`;
  const info = Object.fromEntries((bots || []).map((b) => [b.key, b]));
  const line = (k) => (info[k] ? `${MODE_NAME[info[k].mode]}${info[k].enabled ? "" : " · off"}${info[k].halted ? " · halted" : ""}` : "");
  return (
    <div className="flex flex-wrap gap-2" data-testid="straddle-bot-switch">
      <button type="button" className={btn(bot === "sb")} onClick={() => setBot("sb")} data-testid="straddle-bot-sb">
        <div className="font-semibold">Straddle Bot rules</div><div className="text-[11px] text-slate-400">17:00 · stop on call + put together · {line("sb")}</div>
      </button>
      <button type="button" className={btn(bot === "op")} onClick={() => setBot("op")} data-testid="straddle-bot-op">
        <div className="font-semibold">Option Picks bot <span className="text-amber-300">★ Recommended</span></div><div className="text-[11px] text-slate-400">17:01 · each leg stopped on its own · compounded · {line("op")}</div>
      </button>
    </div>
  );
}

export default function StraddleBot() {
  const [params, setParams] = useSearchParams();
  const bot = params.get("bot") === "op" ? "op" : "sb";
  const { data: fetched0, error } = useFetch(`/straddle?bot=${bot}`, 3000, { topic: null });
  const fetched = fetched0 && (fetched0.bot || "sb") === bot ? fetched0 : null;
  const [pushed, setPushed] = useState(null), [bt, setBt] = useState(null), [tab, setTab] = useState("live"), [variant, setVariant] = useState(FIRST[bot]);
  useEffect(() => {
    setBt(null); setVariant(FIRST[bot]); setPushed(null);
    api.get(`/straddle/backtest?bot=${bot}`).then((r) => setBt(r.data)).catch((e) => toast.error(errText(e)));
  }, [bot]);
  useEffect(() => { setPushed(null); }, [fetched0]);
  const d = pushed && (pushed.bot || "sb") === bot ? pushed : fetched;
  const setBot = (b) => setParams(b === "op" ? { bot: "op" } : {});
  const act = async (fn, ok) => {
    try { const r = await fn(); setPushed(r.data); toast.success(ok); } catch (e) { toast.error(errText(e)); }
  };
  const title = bot === "op" ? "Option Picks bot" : "Straddle Bot";
  if (!d) return <div className="space-y-4"><PageHead title={title} /><BotSwitch bot={bot} setBot={setBot} />{error ? <Note tone="red">Could not load the bot. {error}</Note> : <Empty>Loading…</Empty>}</div>;
  const m = d.live.metrics, s = d.settings, w = d.live.win_check, op = bot === "op";
  return (
    <div className="space-y-4" data-testid="straddle-page">
      <PageHead title={title} sub={BOT_SUB[bot]}
        right={<span className="flex items-center gap-2"><Chip status={d.mode} label={`${MODE_NAME[d.mode]} mode`} testid="straddle-mode-chip" />
          <Chip status={d.halted ? "BLOCKED" : !d.enabled ? "WARN" : d.running ? "OK" : "WARN"} label={d.halted ? "Halted" : !d.enabled ? "Bot off" : d.running ? "Bot running" : "Bot starting"} testid="straddle-run-chip" /></span>} />
      <BotSwitch bot={bot} setBot={setBot} bots={d.bots} />
      {d.halted && <Note tone="red">Trading is halted: {d.halt_reason}. Check the journal, then press "Re-enable trading".</Note>}
      {!d.enabled && !d.halted && <Note tone="amber">The bot is switched off. It will not trade until you switch it on.</Note>}
      {d.mode === "LIVE" && <Note tone="red">Live mode: real orders on your Delta account. Effective leverage {n(d.effective_leverage, 0)}× per leg.</Note>}
      {w?.alert && <Note tone="amber">Win ratio of the last 30 trades is {pc(w.win_ratio, 0)}, within 5 points of the break-even {pc(w.breakeven, 0)}.</Note>}
      <KpiStrip testid="straddle-kpi" items={[
        { label: `${MODE_NAME[d.mode]} equity`, value: usd(d.equity), sub: `from $${n(s.starting_capital, 0)}`, cls: tone(d.equity - s.starting_capital) },
        { label: "Net P&L", value: m.trades ? signedUsd(m.net) : "—", cls: tone(m.net), sub: `${m.trades} trade${m.trades === 1 ? "" : "s"} · ${m.skipped || 0} skipped` },
        { label: "Win ratio", value: m.trades ? pc(m.win_ratio, 0) : "—", sub: m.trades ? `break-even ${pc(m.breakeven_win_ratio, 0)}` : "after the first trades" },
        { label: "Risk : reward", value: m.risk_reward == null ? "—" : `1 : ${n(m.risk_reward, 2)}`, sub: "avg win ÷ avg loss" },
        { label: "Entry slippage", value: d.live.avg_entry_slip == null ? "—" : pc(d.live.avg_entry_slip, 2), sub: `limit ${n(s.max_entry_slip, 1)}% vs mark` },
        { label: "Server clock", value: d.drift_ms == null ? "not checked" : `${n(Math.abs(d.drift_ms), 0)} ms off`, sub: d.drift_source || `checked at ${op ? "16:56" : "16:55"} IST · limit 100 ms`, cls: d.drift_ms != null && Math.abs(d.drift_ms) > 100 ? "text-red-300" : "" },
        { label: "Bot loop delay", value: `${n(d.lag_ms, 0)} ms`, sub: d.own_thread ? "worst today · own thread" : "worst today", cls: d.lag_ms > 250 ? "text-amber-300" : "" },
      ]} />
      <Tabs items={[["live", "Live"], ["compare", "Backtest vs live"], ["monthly", "Monthly"], ["journal", "Journals"], ["settings", "Settings"]]} value={tab} onChange={setTab} testid="straddle-tab" />
      {tab === "live" && <>
        <LiveTrade d={d} />
        {d.rules && (
          <Card title="Rules this bot runs" sub={op ? "Option Picks pick 5, the ★ Recommended setup: compounded capital. Same rules as its backtest." : "The owner's spec."} testid="straddle-rules">
            <ul className="space-y-0.5 text-[12px] text-slate-300">{d.rules.map((r) => <li key={r}>• {r}</li>)}</ul>
          </Card>
        )}
        <Controls d={d} act={act} />
        <Rehearsal d={d} setPushed={setPushed} />
        <Card title="Event log" sub="Every decision: pre-flight, entry, size cuts, stop, expiry, skips and why, halts, errors." testid="straddle-events" pad={false}>
          {d.events.length ? <div className="overflow-x-auto scroll-thin"><table className="desk-table"><tbody>
            {d.events.map((e, i) => <tr key={i}><td className="whitespace-nowrap font-mono text-slate-400">{ist(e.at)}</td><td className={e.level === "error" ? "text-red-300" : e.level === "warn" ? "text-amber-300" : "text-slate-200"}>{e.text}</td></tr>)}
          </tbody></table></div> : <div className="p-3"><Empty>No events yet. The first pre-flight runs at {op ? "16:56" : "16:55"} IST.</Empty></div>}
        </Card>
      </>}
      {tab === "compare" && (bt ? <Compare bt={bt} live={d.live} mode={d.mode} variant={variant} setVariant={setVariant} op={op} /> : <Empty>Loading the backtest…</Empty>)}
      {tab === "monthly" && (bt ? <Monthly bt={bt} live={d.live} mode={d.mode} variant={variant} setVariant={setVariant} op={op} /> : <Empty>Loading the backtest…</Empty>)}
      {tab === "journal" && <Journal key={bot} mode={d.mode} bot={bot} bt={bt} />}
      {tab === "settings" && <SettingsTab d={d} act={act} onImported={setBt} />}
    </div>
  );
}
