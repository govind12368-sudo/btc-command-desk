import React, { useEffect, useMemo, useRef, useState } from "react";
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
const VARIANTS = [["mark_fixed", "Mark · fixed"], ["mark_compound", "Mark · compounded"], ["trade_fixed", "Traded · fixed"], ["trade_compound", "Traded · compounded"]];

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
    ["max_lots", "Maximum lots per leg"], ["live_small_size", "Live: start small (0 or 1)", "1 = Live mode uses leverage 5 at most"]]],
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
          <Fig label="Margin used (estimate)" value={usd(v.margin_used)} sub={v.feed_ok ? "price feed connected" : "price feed reconnecting"} cls={v.feed_ok ? "" : "text-amber-300"} />
        </div>
      </Card>
    );
  }
  const msg = v.phase === "PREFLIGHT" ? `Pre-flight done. Entry in ${cd}. BTC index ${n(v.spot, 1)}, strikes watched: ${(v.strikes || []).map((k) => n(k, 0)).join(", ")}.`
    : v.phase === "SETTLING" ? "Expired. Waiting for Delta's settlement price."
    : t?.status === "CLOSED" ? `Today's trade is closed: ${t.exit_reason === "Stop" ? `stop exit at ${t.exit_time} IST${t.exit_note ? ` (${t.exit_note})` : ""}` : "held to expiry"}.`
    : t?.status === "SKIPPED" ? `Today was skipped: ${t.skip_reason}.`
    : `No open trade. Next entry in ${cd} (17:00 IST).`;
  return <Card title="Live trade" testid="straddle-idle"><p className="text-[13px] text-slate-200">{msg}</p></Card>;
}

function Controls({ d, act }) {
  const [liveBox, setLiveBox] = useState(false), [word, setWord] = useState(""), [killAsk, setKillAsk] = useState(false);
  const r = d.ready;
  return (
    <Card title="Mode and controls" sub="Paper = simulated fills at the real bid/ask, no orders sent. Testnet = Delta's practice exchange. Live = real orders, real money." testid="straddle-controls">
      <div className="flex flex-wrap items-center gap-2">
        {["PAPER", "TESTNET"].map((m) => <Btn key={m} tone={d.mode === m ? "gold" : "ghost"} onClick={() => d.mode !== m && act(() => api.post("/straddle/mode", { mode: m }), `Mode is now ${MODE_NAME[m]}`)} data-testid={`straddle-mode-${m}`}>{MODE_NAME[m]}</Btn>)}
        <Btn tone={d.mode === "LIVE" ? "red" : "ghost"} onClick={() => d.mode !== "LIVE" && setLiveBox(!liveBox)} data-testid="straddle-mode-LIVE">Live</Btn>
        <span className="mx-1 h-5 w-px bg-line" />
        {d.enabled
          ? <Btn tone="ghost" onClick={() => act(() => api.post("/straddle/control", { action: "disable" }), "Bot switched off")} data-testid="straddle-disable">Switch bot off</Btn>
          : <Btn onClick={() => act(() => api.post("/straddle/control", { action: "enable" }), "Bot switched on")} data-testid="straddle-enable">Switch bot on</Btn>}
        {d.halted && <Btn onClick={() => act(() => api.post("/straddle/control", { action: "clear_halt" }), "Halt cleared")} data-testid="straddle-clear-halt">Re-enable trading</Btn>}
        {killAsk
          ? <><Btn tone="red" onClick={() => act(() => api.post("/straddle/control", { action: "kill" }), "Flat. Bot is off.").then(() => setKillAsk(false))} data-testid="straddle-kill-confirm">Yes, flatten now</Btn><Btn tone="ghost" onClick={() => setKillAsk(false)}>Cancel</Btn></>
          : <Btn tone="red" onClick={() => setKillAsk(true)} data-testid="straddle-kill">Kill switch</Btn>}
      </div>
      <p className="mt-2 text-[11.5px] text-slate-400">Kill switch: cancels this strategy's orders, buys back both legs at once and switches the bot off. Other strategies on the same Delta account are not touched.</p>
      {liveBox && d.mode !== "LIVE" && (
        <div className="mt-3 rounded-sm border border-red-500/40 bg-red-500/5 p-3" data-testid="straddle-live-box">
          <ul className="mb-2 space-y-0.5 text-[12px] text-slate-300">
            <li>1. Paper mode ran for {r.paper_days_needed} trading days: {r.paper_days >= r.paper_days_needed ? "yes" : `no (${r.paper_days} so far)`}</li>
            <li>2. Live API key saved (Auto-Trade page, trading permission only, no withdrawal, IP whitelisted): {r.live_key ? "yes" : "no"}</li>
            <li>3. Live starts at leverage 5 at most until you set "Live: start small" to 0.</li>
          </ul>
          <div className="flex flex-wrap items-end gap-2">
            <Field label="Type LIVE to place real orders" hint="Real money."><Input value={word} onChange={(e) => setWord(e.target.value)} data-testid="straddle-live-word" /></Field>
            <Btn tone="red" disabled={word.trim() !== "LIVE"} onClick={() => act(() => api.post("/straddle/mode", { mode: "LIVE", confirm: word }), "Mode is now Live").then(() => { setLiveBox(false); setWord(""); })} data-testid="straddle-live-confirm">Switch to Live</Btn>
          </div>
        </div>
      )}
    </Card>
  );
}

function Compare({ bt, live, mode, variant, setVariant }) {
  const b = bt.variants[variant], lm = live.metrics;
  const btCurve = useMemo(() => b.curve.map((p) => ({ t: p.t, v: p.equity })), [b]);
  const btDd = useMemo(() => b.curve.map((p) => ({ t: p.t, v: p.dd_pct })), [b]);
  const day = (t) => t;
  return (
    <>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <Tabs items={VARIANTS} value={variant} onChange={setVariant} testid="straddle-variant" />
        <span className="text-[11.5px] text-slate-500">Backtest: {bt.source}</span>
      </div>
      <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
        <Card title={`Backtest equity: ${b.name}`} sub={`$${n(bt.inputs.starting_capital, 0)} start, leverage ${n(bt.inputs.leverage, 0)}×`} testid="straddle-bt-equity">
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
      <Card title="Backtest vs the bot, same formulas" sub="Every row uses the formula of your Summary sheet." testid="straddle-compare" pad={false}>
        <div className="overflow-x-auto scroll-thin">
          <table className="desk-table" data-testid="straddle-compare-table">
            <thead><tr><th>Metric</th><th className="num">Backtest ({b.name})</th><th className="num">{MODE_NAME[mode]} (the bot)</th></tr></thead>
            <tbody>
              {METRICS.map(([label, key, f]) => (
                <tr key={key}><td>{label}</td><td className="num">{b.metrics[key] == null && key === "skipped" ? "0" : f(b.metrics[key])}</td><td className="num">{lm.trades || key === "days" || key === "skipped" ? f(lm[key]) : "—"}</td></tr>
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

function Monthly({ bt, live, mode, variant, setVariant }) {
  const b = bt.variants[variant];
  const by = Object.fromEntries(live.monthly.map((m) => [m.month, m]));
  const months = [...new Set([...b.monthly.map((m) => m.month), ...live.monthly.map((m) => m.month)])].sort().reverse();
  const bb = Object.fromEntries(b.monthly.map((m) => [m.month, m]));
  const cells = (m) => (m ? <><td className="num">{usd(m.before)}</td><td className={`num ${tone(m.net)}`}>{signedUsd(m.net)}</td><td className="num">{usd(m.after)}</td><td className="num">{pc(m.win_ratio, 0)}</td><td className="num">{m.stops}</td></>
    : <><td className="num">—</td><td className="num">—</td><td className="num">—</td><td className="num">—</td><td className="num">—</td></>);
  return (
    <>
      <Tabs items={VARIANTS} value={variant} onChange={setVariant} testid="straddle-variant-m" />
      <Card title="Monthly" sub={`Left: backtest (${b.name}). Right: the bot in ${MODE_NAME[mode]} mode.`} testid="straddle-monthly" pad={false}>
        <div className="overflow-x-auto scroll-thin">
          <table className="desk-table">
            <thead>
              <tr><th /><th colSpan={5} className="text-center">Backtest</th><th colSpan={5} className="text-center">{MODE_NAME[mode]}</th></tr>
              <tr><th>Month</th>{[0, 1].map((k) => <React.Fragment key={k}><th className="num">Capital before</th><th className="num">Net P&L</th><th className="num">Capital after</th><th className="num">Win ratio</th><th className="num">Stops</th></React.Fragment>)}</tr>
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

function Journal({ mode }) {
  const [src, setSrc] = useState("mark_fixed"), [page, setPage] = useState(null), [limit, setLimit] = useState(100);
  useEffect(() => {
    let on = true;
    api.get(`/straddle/journal?src=${src}&limit=${limit}`).then((r) => on && setPage(r.data)).catch((e) => toast.error(errText(e)));
    return () => { on = false; };
  }, [src, limit]);
  const csv = async () => {
    try {
      const r = await api.get(`/straddle/journal.csv?src=${src}`, { responseType: "blob" });
      const u = URL.createObjectURL(r.data), a = document.createElement("a");
      a.href = u; a.download = `straddle_${src.toLowerCase()}_journal.csv`; a.click(); URL.revokeObjectURL(u);
    } catch (e) { toast.error(errText(e)); }
  };
  const items = [...VARIANTS.map(([k, l]) => [k, `Backtest: ${l}`]), [mode, `${MODE_NAME[mode]} journal (the bot)`]];
  return (
    <>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <Tabs items={items} value={src} onChange={(k) => { setPage(null); setLimit(100); setSrc(k); }} testid="straddle-journal-src" />
        <Btn tone="ghost" onClick={csv} data-testid="straddle-journal-csv">Download CSV (all rows)</Btn>
      </div>
      <Card title="Trade journal" sub="Newest first. Same columns as your workbook; the bot's journal adds order IDs, fill times, latency, slippage and flags." testid="straddle-journal" pad={false}>
        {!page ? <div className="p-3"><Empty>Loading…</Empty></div> : !page.rows.length ? <div className="p-3"><Empty>No trades yet in this journal. The first entry is at 17:00 IST.</Empty></div> : (
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
  const dirty = Object.keys(v).filter((k) => String(v[k]) !== String(d.settings[k]));
  const save = () => act(() => api.post("/straddle/settings", { patch: Object.fromEntries(dirty.map((k) => [k, d.choices[k] ? v[k] : Number(v[k])])) }), "Settings saved");
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
              {rows.map(([k, label, hint]) => (
                <Field key={k} label={label} hint={hint || (d.bounds[k] ? `${d.bounds[k][0]} to ${n(d.bounds[k][1], 0)} · default ${d.defaults[k]}` : `default ${d.defaults[k]}`)}>
                  {d.choices[k]
                    ? <Select value={v[k]} onChange={(e) => setV({ ...v, [k]: e.target.value })} options={d.choices[k]} data-testid={`straddle-set-${k}`} />
                    : <Input type="number" value={v[k]} onChange={(e) => setV({ ...v, [k]: e.target.value })} data-testid={`straddle-set-${k}`} />}
                </Field>
              ))}
            </div>
          </div>
        ))}
      </Card>
      <Card title="Backtest workbook" sub="Import a newer version of your Excel backtest (sheets J_Mark_Fixed and J_Trade_Fixed). The numbers are re-computed from its day-by-day prices." testid="straddle-import">
        <input ref={file} type="file" accept=".xlsx" className="hidden" onChange={upload} data-testid="straddle-import-file" />
        <Btn tone="ghost" disabled={busy} onClick={() => file.current?.click()} data-testid="straddle-import-btn">{busy ? "Importing…" : "Import Excel backtest"}</Btn>
      </Card>
      <Card title="Change log" testid="straddle-changes" pad={false}>
        {d.changes.length ? (
          <div className="overflow-x-auto scroll-thin"><table className="desk-table"><thead><tr><th>When</th><th>Setting</th><th className="num">From</th><th className="num">To</th><th>By</th></tr></thead>
            <tbody>{d.changes.map((c, i) => <tr key={i}><td className="font-mono">{ist(c.at)}</td><td>{c.key}</td><td className="num">{String(c.old)}</td><td className="num">{String(c.new)}</td><td>{c.by}</td></tr>)}</tbody></table></div>
        ) : <div className="p-3"><Empty>No setting has been changed yet.</Empty></div>}
      </Card>
    </>
  );
}

export default function StraddleBot() {
  const { data: fetched, error } = useFetch("/straddle", 3000, { topic: null });
  const [pushed, setPushed] = useState(null), [bt, setBt] = useState(null), [tab, setTab] = useState("live"), [variant, setVariant] = useState("mark_fixed");
  useEffect(() => { api.get("/straddle/backtest").then((r) => setBt(r.data)).catch((e) => toast.error(errText(e))); }, []);
  useEffect(() => { setPushed(null); }, [fetched]);
  const d = pushed || fetched;
  const act = async (fn, ok) => {
    try { const r = await fn(); setPushed(r.data); toast.success(ok); } catch (e) { toast.error(errText(e)); }
  };
  if (!d) return <div className="space-y-4"><PageHead title="Straddle Bot" />{error ? <Note tone="red">Could not load the straddle bot. {error}</Note> : <Empty>Loading…</Empty>}</div>;
  const m = d.live.metrics, s = d.settings, w = d.live.win_check;
  return (
    <div className="space-y-4" data-testid="straddle-page">
      <PageHead title="Straddle Bot" sub="Sell the ATM BTC call + put on Delta India at 17:00 IST, stop at 2 × premium, otherwise hold to the 17:30 expiry. Admin only."
        right={<span className="flex items-center gap-2"><Chip status={d.mode} label={`${MODE_NAME[d.mode]} mode`} testid="straddle-mode-chip" />
          <Chip status={d.halted ? "BLOCKED" : !d.enabled ? "WARN" : d.running ? "OK" : "WARN"} label={d.halted ? "Halted" : !d.enabled ? "Bot off" : d.running ? "Bot running" : "Bot starting"} testid="straddle-run-chip" /></span>} />
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
        { label: "Server clock", value: d.drift_ms == null ? "not checked" : `${n(Math.abs(d.drift_ms), 0)} ms off`, sub: "checked at 16:55 IST · limit 100 ms", cls: d.drift_ms != null && Math.abs(d.drift_ms) > 100 ? "text-red-300" : "" },
      ]} />
      <Tabs items={[["live", "Live"], ["compare", "Backtest vs live"], ["monthly", "Monthly"], ["journal", "Journals"], ["settings", "Settings"]]} value={tab} onChange={setTab} testid="straddle-tab" />
      {tab === "live" && <>
        <LiveTrade d={d} />
        <Controls d={d} act={act} />
        <Card title="Event log" sub="Every decision: pre-flight, entry, size cuts, stop, expiry, skips and why, halts, errors." testid="straddle-events" pad={false}>
          {d.events.length ? <div className="overflow-x-auto scroll-thin"><table className="desk-table"><tbody>
            {d.events.map((e, i) => <tr key={i}><td className="whitespace-nowrap font-mono text-slate-400">{ist(e.at)}</td><td className={e.level === "error" ? "text-red-300" : e.level === "warn" ? "text-amber-300" : "text-slate-200"}>{e.text}</td></tr>)}
          </tbody></table></div> : <div className="p-3"><Empty>No events yet. The first pre-flight runs at 16:55 IST.</Empty></div>}
        </Card>
      </>}
      {tab === "compare" && (bt ? <Compare bt={bt} live={d.live} mode={d.mode} variant={variant} setVariant={setVariant} /> : <Empty>Loading the backtest…</Empty>)}
      {tab === "monthly" && (bt ? <Monthly bt={bt} live={d.live} mode={d.mode} variant={variant} setVariant={setVariant} /> : <Empty>Loading the backtest…</Empty>)}
      {tab === "journal" && <Journal mode={d.mode} />}
      {tab === "settings" && <SettingsTab d={d} act={act} onImported={setBt} />}
    </div>
  );
}
