import React, { useEffect, useState } from "react";
import { toast } from "sonner";
import { useFetch } from "@/hooks/useFetch";
import { api, errText } from "@/lib/api";
import { Card, PageHead, Note, Chip } from "@/components/desk/ui";
import { Btn, Field, Input } from "@/components/desk/forms";
import { n, signedUsd, ist } from "@/lib/fmt";
import { LineChart, VIZ } from "@/components/desk/charts";

// P&L #5 stays in the engine (owner, 23 Nov 2026) but is not shown here: it fires on exactly the same candle as P&L #1,
// which has priority, so it never takes a trade and every total is unchanged without it.
const HIDDEN = new Set(["pl5"]);
const hidden = (x) => HIDDEN.has(x?.key) || HIDDEN.has(x?.strategy) || x?.wb === "P&L #5";

const pnlTone = (v) => (v > 0 ? "text-teal-300" : v < 0 ? "text-red-300" : "text-slate-300");
const pctTxt = (v) => (v == null ? "—" : `${Math.round(v * 100)}%`);
const RESULT_TONE = { Target: "text-teal-300", Stop: "text-red-300", "Time exit": "text-amber-300", Open: "text-sky-300" };
const MODE_CHIP = { PAPER: "IDLE", TESTNET: "WAIT", LIVE: "BLOCKED" };
const MODE_NAME = { PAPER: "Paper", TESTNET: "Testnet", LIVE: "Live (real money)" };
const hold = (h) => (h === 168 ? "7 days" : `${h} h`);

function Stat({ label, value, tone = "text-slate-50", sub, testid }) {
  return (
    <div className="rounded-lg border border-line bg-panel2/60 px-3 py-2.5" data-testid={testid}>
      <div className="text-[11.5px] text-slate-400">{label}</div>
      <div className={`font-mono text-lg font-bold ${tone}`}>{value}</div>
      {sub && <div className="text-[11px] text-slate-500">{sub}</div>}
    </div>
  );
}

function Totals({ s, testid, viewer }) {
  if (!s) return null;
  return (
    <div className={`grid grid-cols-2 gap-2 sm:grid-cols-3 ${viewer ? "lg:grid-cols-5" : "lg:grid-cols-6"}`} data-testid={testid}>
      <Stat label="Trades (closed)" value={s.trades} sub={`${s.wins} won · ${s.losses} lost`} />
      <Stat label="Win ratio" value={pctTxt(s.win_rate)} sub="net after fees > 0" />
      <Stat label="Gross P&L" value={signedUsd(s.gross)} tone={pnlTone(s.gross)} sub="before fees" />
      <Stat label="Fees" value={signedUsd(-s.fees)} tone="text-slate-300" sub="0.05% each side + 18% GST" />
      <Stat label="Net P&L" value={signedUsd(s.net)} tone={pnlTone(s.net)} sub={s.per_trade != null ? `${signedUsd(s.per_trade)} a trade` : null} />
      {!viewer && <Stat label="Max drawdown" value={signedUsd(s.max_dd)} tone="text-red-300" sub="closed trades, from the peak" />}
    </div>
  );
}

function StratTable({ j, strategies, viewer }) {
  const all = [...(j.per_strategy || []).filter((s) => !hidden(s)), { key: "all", wb: "Combined", name: "One trade at a time", ...j.combined }];
  return (
    <div className="overflow-x-auto scroll-thin">
      <table className="desk-table">
        <thead><tr><th>Priority</th><th>Strategy</th><th className="num">Trades</th><th className="num">Won</th><th className="num">Lost</th><th className="num">Win %</th><th className="num">Gross</th><th className="num">Fees</th><th className="num">Net P&L</th><th className="num">Per trade</th>{!viewer && <th className="num">Max DD</th>}</tr></thead>
        <tbody>{all.map((s) => {
          const st = strategies.find((x) => x.key === s.key);
          return (
            <tr key={s.key} className={s.key === "all" ? "border-t-2 border-line2 font-semibold" : ""}>
              <td className="font-mono">{st ? st.priority : "—"}</td>
              <td>{s.wb}<div className="text-[11px] font-normal text-slate-500">{st ? st.name : s.name}</div></td>
              <td className="num">{s.trades}</td><td className="num">{s.wins}</td><td className="num">{s.losses}</td><td className="num">{pctTxt(s.win_rate)}</td>
              <td className={`num ${pnlTone(s.gross)}`}>{signedUsd(s.gross)}</td><td className="num">{signedUsd(-s.fees)}</td>
              <td className={`num font-semibold ${pnlTone(s.net)}`}>{signedUsd(s.net)}</td><td className={`num ${pnlTone(s.per_trade)}`}>{signedUsd(s.per_trade)}</td>
              {!viewer && <td className="num text-red-300">{signedUsd(s.max_dd)}</td>}
            </tr>);
        })}</tbody>
      </table>
    </div>
  );
}

function YearTable({ rows, wbs, viewer }) {
  return (
    <div className="overflow-x-auto scroll-thin">
      <table className="desk-table" data-testid="elite-yearly">
        <thead><tr><th>Year</th><th className="num">Trades</th><th className="num">Win %</th><th className="num">Gross</th><th className="num">Fees</th><th className="num">Net P&L</th>{!viewer && <th className="num">Max DD</th>}{wbs.map((w) => <th key={w} className="num">{w}</th>)}<th className="num">Cumulative</th></tr></thead>
        <tbody>{rows.map((y) => (
          <tr key={y.year} className={y.year === "Total till date" ? "border-t-2 border-line2 font-semibold" : ""}>
            <td>{y.year}</td><td className="num">{y.trades}</td><td className="num">{pctTxt(y.win_rate)}</td>
            <td className={`num ${pnlTone(y.gross)}`}>{signedUsd(y.gross, 0)}</td><td className="num">{signedUsd(-y.fees, 0)}</td>
            <td className={`num font-semibold ${pnlTone(y.net)}`}>{signedUsd(y.net, 0)}</td>{!viewer && <td className="num text-red-300">{signedUsd(y.max_dd, 0)}</td>}
            {wbs.map((w) => <td key={w} className={`num ${pnlTone(y.by_strategy[w])}`}>{signedUsd(y.by_strategy[w], 0)}</td>)}
            <td className={`num ${pnlTone(y.cumulative)}`}>{signedUsd(y.cumulative, 0)}</td>
          </tr>))}
        </tbody>
      </table>
    </div>
  );
}

function MonthTable({ rows, wbs }) {
  return (
    <div className="max-h-[520px] overflow-auto scroll-thin">
      <table className="desk-table" data-testid="elite-monthly">
        <thead className="sticky top-0 bg-panel2"><tr><th>Month</th><th className="num">Trades</th><th className="num">Win %</th><th className="num">Gross</th><th className="num">Fees</th><th className="num">Net P&L</th>{wbs.map((w) => <th key={w} className="num">{w}</th>)}<th className="num">Cumulative</th></tr></thead>
        <tbody>{[...rows].reverse().map((m) => (
          <tr key={m.month} className={m.net < 0 ? "bg-red-500/10" : ""}>
            <td className={`font-mono ${m.net < 0 ? "text-red-300" : ""}`}>{m.month}</td><td className="num">{m.trades}</td><td className="num">{pctTxt(m.win_rate)}</td>
            <td className={`num ${pnlTone(m.gross)}`}>{signedUsd(m.gross, 0)}</td><td className="num">{signedUsd(-m.fees, 0)}</td>
            <td className={`num font-semibold ${m.net < 0 ? "text-red-400" : pnlTone(m.net)}`}>{signedUsd(m.net, 0)}</td>
            {wbs.map((w) => <td key={w} className={`num ${pnlTone(m.by_strategy[w])}`}>{signedUsd(m.by_strategy[w], 0)}</td>)}
            <td className={`num ${pnlTone(m.cumulative)}`}>{signedUsd(m.cumulative, 0)}</td>
          </tr>))}
        </tbody>
      </table>
    </div>
  );
}

function TradeTable({ rows, limit, more }) {
  return (
    <div className="overflow-x-auto scroll-thin">
      <table className="desk-table" data-testid="elite-trades">
        <thead><tr><th>Entry (IST)</th><th>Strategy</th><th>Side</th><th>Market</th><th className="num">Entry</th><th className="num">SL</th><th className="num">TP</th><th>Result</th><th>Exit (IST)</th><th className="num">Exit</th><th className="num">Gross</th><th className="num">Fees</th><th className="num">Net</th></tr></thead>
        <tbody>{rows.filter((r) => !hidden(r)).slice(0, limit).map((r) => (
          <tr key={`${r.strategy}-${r.signal_t}`} title={r.reason}>
            <td className="font-mono">{r.entry_ist}</td><td>{r.wb}<div className="text-[11px] text-slate-500">{r.period === "Backtest history" ? "backtest" : "server candles"}</div></td>
            <td className={r.side === "LONG" ? "text-teal-300" : "text-red-300"}>{r.side}</td><td>{r.market}{r.chg30 != null ? ` ${(r.chg30 * 100).toFixed(1)}%` : ""}</td>
            <td className="num">{n(r.entry, 1)}</td><td className="num">{n(r.sl, 1)}</td><td className="num">{n(r.tp, 1)}</td>
            <td className={RESULT_TONE[r.result] || ""}>{r.result}</td><td className="font-mono">{r.exit_ist || "—"}</td>
            <td className="num">{r.exit_price != null ? n(r.exit_price, 1) : r.mark != null ? `${n(r.mark, 1)} now` : "—"}</td>
            <td className={`num ${pnlTone(r.gross)}`}>{signedUsd(r.gross)}</td><td className="num">{signedUsd(-r.fees)}</td>
            <td className={`num font-semibold ${pnlTone(r.net)}`}>{signedUsd(r.net)}</td>
          </tr>))}
        </tbody>
      </table>
      {rows.length > limit && <div className="p-2 text-center"><Btn tone="ghost" onClick={more}>Show 100 more ({rows.length - limit} left)</Btn></div>}
    </div>
  );
}

function Journal({ j, title, strategies, note, viewer }) {
  const [limit, setLimit] = useState(100);
  if (!j) return <Note>Working out the journal… this takes about three minutes after a restart.</Note>;
  const wbs = strategies.map((s) => s.wb);
  let eq = 0;
  const curve = [...j.trades].filter((r) => r.status === "CLOSED").sort((a, b) => a.exit_t - b.exit_t).map((r) => ({ t: r.exit_ist, v: (eq += r.net) }));
  const open = j.trades.find((r) => r.status === "OPEN");
  return (
    <div className="space-y-4">
      {note}
      <Totals s={j.combined} testid="elite-journal-totals" viewer={viewer} />
      {open && <Note>The backtest has an open {open.wb} {open.side} from {open.entry_ist} at {n(open.entry, 1)} (now {signedUsd(open.net)}). Open trades are not in the totals.</Note>}
      <Card title={`${title}: by strategy`} sub={viewer ? "The strategy rows add up to the combined row." : "One trade at a time for the whole account. The strategy rows add up to the combined row."}>
        <StratTable j={j} strategies={strategies} viewer={viewer} />
      </Card>
      <Card title="Yearly" sub="By entry date (IST). Net after fees.">
        <YearTable rows={j.yearly} wbs={wbs} viewer={viewer} />
      </Card>
      {curve.length > 1 && (
        <Card title="Cumulative net P&L" sub="Closed trades, after fees">
          <LineChart series={[{ name: "Net P&L $", color: VIZ.s1, points: curve }]} xFmt={(v) => v} yFmt={(v) => n(v, 0)} unit=" $" height={160} />
        </Card>
      )}
      <Card title="Monthly" sub="Newest first. Months that lost money are red.">
        <MonthTable rows={j.monthly} wbs={wbs} />
      </Card>
      <Card title="Trades" sub={`Newest first · ${j.trades.length} rows.${viewer ? "" : " Hover a row for the reason the signal fired."}`}>
        <TradeTable rows={j.trades} limit={limit} more={() => setLimit(limit + 100)} />
      </Card>
    </div>
  );
}

function RealMoney({ lv, act, busy }) {
  const [box, setBox] = useState(null);
  const [word, setWord] = useState("");
  const rm = lv.real_money || {};
  const yes = (b) => <span className={b ? "text-teal-300" : "text-slate-400"}>{b ? "Yes" : "No"}</span>;
  const setMode = (m, c) => act(() => api.post("/elite/mode", { mode: m, confirm: c }), `Elite 4 Edge is now in ${MODE_NAME[m]}`).then(() => { setBox(null); setWord(""); });
  return (
    <Card title="Real-money switch" sub="OFF (Paper) by default. Only an admin can change it, and only with no Elite trade open." testid="elite-real-money">
      <div className="flex flex-wrap gap-2">
        {["PAPER", "TESTNET", "LIVE"].map((m) => (
          <Btn key={m} tone={lv.mode === m ? "gold" : m === "LIVE" ? "red" : "ghost"} disabled={busy || lv.mode === m} data-testid={`elite-mode-${m}`}
            onClick={() => (m === "PAPER" ? setMode("PAPER") : setBox(m))}>{MODE_NAME[m]}{lv.mode === m ? " (current)" : ""}</Btn>
        ))}
      </div>
      {box && (
        <div className="mt-3 flex flex-wrap items-end gap-2 rounded border border-red-500/30 bg-red-500/5 p-3">
          <Field label={`Type ${box} to switch`} hint={box === "LIVE" ? "Real orders on your Delta Exchange account: market entry with one stop and one target (bracket)." : "Orders on the Delta testnet."}>
            <Input value={word} onChange={(e) => setWord(e.target.value)} placeholder={box} autoComplete="off" data-testid="elite-mode-word" />
          </Field>
          <Btn tone="red" disabled={busy || word !== box} onClick={() => setMode(box, word)}>Switch to {MODE_NAME[box]}</Btn>
          <Btn tone="ghost" onClick={() => { setBox(null); setWord(""); }}>Cancel</Btn>
        </div>
      )}
      <ul className="mt-3 space-y-1 text-[11.5px] text-slate-400" data-testid="elite-checklist">
        <li>Testnet key in the server environment (ELITE_DELTA_TESTNET_API_KEY / _SECRET, or DELTA_TESTNET_API_KEY / _SECRET): {yes(rm.testnet_key)}</li>
        <li>Live key in the server environment (ELITE_DELTA_API_KEY / _SECRET, or DELTA_TRADE_API_KEY / _SECRET): {yes(rm.live_key)}</li>
        <li>One completed Elite Testnet trade (needed before Live): {yes(rm.testnet_trade_done)}</li>
        <li>Auto-Trade is not in the same mode (both bots use the same Delta account): Auto-Trade is in {rm.autotrade_mode || "—"}{rm.autotrade_mode === "LIVE" ? <span className="text-amber-300"> – Elite cannot go Live until Auto-Trade leaves Live</span> : ""}</li>
      </ul>
      <Note tone="plain">Keys pasted on the Auto-Trade page are never used here. Each switch checks the key from this server, that the account is flat, that 10x leverage is set and that the margin for one trade (about $1,000) is there.</Note>
    </Card>
  );
}

function Live({ lv, isAdmin, reload }) {
  const viewer = !isAdmin;   // users see trades and results only (the server also removes the rest from their data)
  const [busy, setBusy] = useState(false);
  const [sure, setSure] = useState(false);
  const act = async (fn, ok) => {
    setBusy(true);
    try { await fn(); toast.success(ok); await reload(); } catch (e) { toast.error(errText(e)); } finally { setBusy(false); }
  };
  const b = lv.book;
  const t = lv.open_trade;
  const c = lv.compare;
  const closed = lv.trades.filter((x) => x.status === "CLOSED");
  return (
    <div className="space-y-4">
      {lv.mode === "PAPER" && <Note>{viewer ? "Paper mode: no order is sent to the exchange." : "Paper mode: trades are opened at the live Delta price when a 1h or 4h candle closes with a signal. No order is sent to the exchange."}</Note>}
      {lv.mode !== "PAPER" && <Note tone="red">{MODE_NAME[lv.mode]} mode: orders are placed on Delta Exchange{lv.mode === "TESTNET" ? " testnet" : ""}.</Note>}
      {lv.halt && <Note tone="amber">{viewer ? `Trading is stopped (${ist(lv.halt.at)}). No new trades until it is resumed.` : `Trading stopped: ${lv.halt.reason} (${ist(lv.halt.at)}). New signals are skipped until an admin presses Resume.`}</Note>}
      <div className="grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-5" data-testid="elite-live-stats">
        <Stat label="Paper capital" value={`$${n(lv.paper_capital, 0)}`} sub={`balance now $${n(lv.paper_balance, 2)}`} />
        <Stat label="P&L (closed + open)" value={signedUsd(b.pnl)} tone={pnlTone(b.pnl)} sub={`closed ${signedUsd(b.realized)} · open ${signedUsd(b.unrealized)}`} />
        <Stat label="Closed trades" value={lv.paper_stats.trades} sub={`${pctTxt(lv.paper_stats.win_rate)} won · net ${signedUsd(lv.paper_stats.net)}`} />
        <Stat label="Open trades" value={t ? "1 of 1" : "0 of 1"} tone={t ? "text-sky-300" : "text-slate-300"} sub="one trade at a time" />
        <Stat label="BTC (Delta)" value={lv.price ? n(lv.price, 1) : "—"} sub={`live since ${ist(lv.live_start)}`} />
      </div>

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <Card title="Open position" sub={t ? (viewer ? t.wb : `${t.wb} · ${t.name}`) : (viewer ? "No open trade." : "No open trade: the next allowed signal is taken.")} testid="elite-open">
          {t ? (
            <div className="grid grid-cols-1 gap-x-6 gap-y-1 text-[12.5px] sm:grid-cols-2">
              {[["Side", <span className={t.side === "LONG" ? "text-teal-300" : "text-red-300"}>{t.side} {t.size.toFixed(3)} BTC</span>], ["Entry", `${n(t.entry, 1)} at ${t.entry_ist}`],
                ["Stop loss", n(t.sl, 1)], ["Target", viewer ? n(t.tp, 1) : `${n(t.tp, 1)} (1:${t.rr})`], ["Live P&L (after fees)", <span className={pnlTone(t.unrealized)}>{signedUsd(t.unrealized)}</span>],
                ...(viewer ? [] : [["Time exit in", `${t.time_left_h} h`], ["Market", `${t.market}${t.chg30 != null ? ` ${(t.chg30 * 100).toFixed(1)}%` : ""}`]]), ["Orders", t.mode === "PAPER" ? "paper" : t.bracket_confirmed ? "stop + target confirmed" : "checking stop + target"]]
                .map(([k, v]) => <div key={k} className="flex justify-between gap-3 border-b border-line/60 py-1"><span className="text-slate-400">{k}</span><span className="font-mono text-slate-200">{v}</span></div>)}
              {!viewer && <div className="pt-1 text-[11.5px] text-slate-400 sm:col-span-2">Why: {t.reason}</div>}
            </div>
          ) : <div className="text-[12.5px] text-slate-400">{viewer ? "The next trade appears here as soon as it opens." : "Signals are checked at every 1h close (Row 9) and every 4h close (P&L #1, Row 18: 01:30, 05:30, 09:30, 13:30, 17:30, 21:30 IST)."}</div>}
        </Card>
        {isAdmin && (
          <Card title="Controls" sub="Stop all closes the open trade at market, cancels orders and stops new trades." testid="elite-controls">
            <div className="flex flex-wrap gap-2">
              {sure
                ? <><Btn tone="red" disabled={busy} onClick={() => act(() => api.post("/elite/control", { action: "stop_all" }), "Elite stopped: open trade closed, no new trades").then(() => setSure(false))} data-testid="elite-stop-confirm">Yes, stop all</Btn>
                  <Btn tone="ghost" onClick={() => setSure(false)}>Keep running</Btn></>
                : <Btn tone="red" disabled={busy} onClick={() => setSure(true)} data-testid="elite-stop-all">Stop all</Btn>}
              {lv.halt && <Btn tone="teal" disabled={busy} onClick={() => act(() => api.post("/elite/control", { action: "resume" }), "Resumed")} data-testid="elite-resume">Resume</Btn>}
            </div>
            <p className="mt-2 text-[11.5px] text-slate-400">Resume starts measuring the drawdown again from the P&L at that moment.</p>
          </Card>
        )}
      </div>

      {isAdmin && <RealMoney lv={lv} act={act} busy={busy} />}

      {!viewer && (
      <Card title="Signals" sub="Every signal since the live start: taken, or skipped with the reason." testid="elite-signals">
        <div className="max-h-[420px] overflow-auto scroll-thin">
          <table className="desk-table">
            <thead className="sticky top-0 bg-panel2"><tr><th>Candle close (IST)</th><th>Strategy</th><th>Side</th><th>Market</th><th>Status</th><th>Reason</th></tr></thead>
            <tbody>{lv.signals.filter((s) => !hidden(s)).map((s) => (
              <tr key={s.id} title={s.why}><td className="font-mono">{s.ist}</td><td>{s.wb}</td><td className={s.side === "LONG" ? "text-teal-300" : "text-red-300"}>{s.side}</td>
                <td>{s.market}</td><td className={s.status === "TAKEN" ? "text-teal-300" : "text-slate-400"}>{s.status}</td><td className="text-[12px]">{s.reason || s.why}</td></tr>))}
            </tbody>
          </table>
          {!lv.signals.length && <div className="p-4 text-center text-xs text-slate-500">No signals since the live start yet.</div>}
        </div>
      </Card>
      )}

      <Card title="Live trades" sub={`${lv.mode} and earlier modes, newest first.`} testid="elite-live-trades">
        <div className="overflow-x-auto scroll-thin">
          <table className="desk-table">
            <thead><tr><th>Mode</th><th>Entry (IST)</th><th>Strategy</th><th>Side</th><th className="num">Entry</th><th className="num">SL</th><th className="num">TP</th><th>Result</th><th>Exit (IST)</th><th className="num">Exit</th><th className="num">Fees</th><th className="num">Net</th></tr></thead>
            <tbody>{lv.trades.filter((r) => !hidden(r)).map((r) => (
              <tr key={r.id} title={r.reason}><td>{r.mode}</td><td className="font-mono">{r.entry_ist}</td><td>{r.wb}</td><td className={r.side === "LONG" ? "text-teal-300" : "text-red-300"}>{r.side}</td>
                <td className="num">{n(r.entry, 1)}</td><td className="num">{n(r.sl, 1)}</td><td className="num">{n(r.tp, 1)}</td>
                <td className={RESULT_TONE[r.result] || "text-sky-300"}>{r.result || "Open"}</td><td className="font-mono">{r.exit_ist || "—"}</td>
                <td className="num">{r.exit_price != null ? n(r.exit_price, 1) : "—"}</td><td className="num">{r.fees != null ? signedUsd(-r.fees) : "—"}</td>
                <td className={`num font-semibold ${pnlTone(r.net ?? r.unrealized)}`}>{r.status === "OPEN" ? `${signedUsd(t?.unrealized)} open` : signedUsd(r.net)}</td></tr>))}
            </tbody>
          </table>
          {!lv.trades.length && <div className="p-4 text-center text-xs text-slate-500">No live trades yet.</div>}
        </div>
      </Card>

      {!viewer && c && (
      <Card title="Paper vs backtest" sub="Each live signal next to what the backtest (same code, Delta 5-minute candles) did with the same candle." testid="elite-compare">
        <div className="mb-2 grid grid-cols-2 gap-2 lg:grid-cols-4">
          <Stat label="Live paper net (closed)" value={signedUsd(c.live_net_all)} tone={pnlTone(c.live_net_all)} sub={`${closed.filter((x) => x.mode === "PAPER").length} trades`} />
          <Stat label="Backtest net, same period" value={signedUsd(c.bt_net_all)} tone={pnlTone(c.bt_net_all)} sub={`${c.bt_trades_all} trades entered since the live start`} />
          <Stat label="Same signals: live" value={signedUsd(c.live_net_same_signals)} tone={pnlTone(c.live_net_same_signals)} sub={`${c.matched} trades taken by both`} />
          <Stat label="Same signals: backtest" value={signedUsd(c.bt_net_same_signals)} tone={pnlTone(c.bt_net_same_signals)} sub="difference = entry price and fill timing" />
        </div>
        <div className="max-h-[420px] overflow-auto scroll-thin">
          <table className="desk-table">
            <thead className="sticky top-0 bg-panel2"><tr><th>Candle close</th><th>Strategy</th><th>Side</th><th>Live</th><th className="num">Live entry</th><th className="num">Live net</th><th>Backtest</th><th className="num">BT entry</th><th className="num">BT net</th></tr></thead>
            <tbody>{c.rows.filter((r) => !hidden(r)).map((r) => (
              <tr key={`${r.wb}-${r.ist}`}><td className="font-mono">{r.ist}</td><td>{r.wb}</td><td>{r.side}</td>
                <td className={r.live_status === "TAKEN" ? "text-teal-300" : "text-slate-400"} title={r.live_reason}>{r.live_status === "TAKEN" ? (r.live_result || "Open") : "Skipped"}</td>
                <td className="num">{n(r.live_entry, 1)}</td><td className={`num ${pnlTone(r.live_net)}`}>{signedUsd(r.live_net)}</td>
                <td className={r.bt_status === "TAKEN" ? "text-teal-300" : "text-slate-400"} title={r.bt_reason}>{r.bt_status === "TAKEN" ? r.bt_result : "Skipped"}</td>
                <td className="num">{n(r.bt_entry, 1)}</td><td className={`num ${pnlTone(r.bt_net)}`}>{signedUsd(r.bt_net)}</td></tr>))}
            </tbody>
          </table>
          {!c.rows.length && <div className="p-4 text-center text-xs text-slate-500">Nothing to compare yet.</div>}
        </div>
        <Note tone="plain">The two can take different trades: live paper started flat at {ist(lv.live_start)}, while the backtest may still have been in a trade then (so it skips signals the live book takes). Hover Skipped for the reason.</Note>
      </Card>
      )}

      {!viewer && (
      <Card title="Event log" sub="Starts, entries, exits, skips, controls and errors." testid="elite-events">
        <div className="max-h-[300px] overflow-auto scroll-thin text-[12px]">
          {lv.events.filter((e) => !String(e.text || "").startsWith("P&L #5 ")).map((e, i) => <div key={i} className={`border-b border-line/60 py-1 ${e.level === "error" ? "text-red-300" : e.level === "warn" ? "text-amber-300" : "text-slate-300"}`}><span className="font-mono text-slate-500">{ist(e.at)}</span> {e.text}</div>)}
        </div>
      </Card>
      )}
    </div>
  );
}

export default function EliteEdge() {
  const { data: d } = useFetch("/elite", 120000);
  useEffect(() => { document.title = "Elite 4 Edge"; }, []);
  if (!d) return <div className="text-sm text-slate-400">Loading Elite 4 Edge…</div>;
  return d.is_admin ? <EliteAdmin d={d} /> : <EliteUser d={d} />;
}

// Users: journal A only (1 Jan 2023 → today). No journal B, no live paper trading, no rules (the server does not send them).
function EliteUser({ d }) {
  const strategies = (d.strategies || []).filter((s) => !hidden(s)).map((s, i) => ({ ...s, priority: i + 1 }));
  return (
    <div className="space-y-4" data-testid="elite-page">
      <PageHead title="Elite 4 Edge" sub={`Backtest journal, 1 Jan 2023 → today, net after fees. Updated ${ist(d.computed_at)}`} />
      {d.error && <Note tone="red">The latest update is delayed. It retries automatically.</Note>}
      <Journal j={d.A} title="1 Jan 2023 → today" strategies={strategies} viewer note={<Note>Backtest trades, not live trades. Net results are after fees.</Note>} />
    </div>
  );
}

function EliteAdmin({ d }) {
  const { data: lvd, reload } = useFetch("/elite/live", 3000);
  const [tab, setTab] = useState("C");
  const lv = lvd?.live || d.live;
  const strategies = (d.strategies || []).filter((s) => !hidden(s)).map((s, i) => ({ ...s, priority: i + 1 }));
  const btn = (on) => `rounded px-3 py-1.5 text-xs font-semibold ${on ? "bg-amber-500 text-slate-950" : "border border-line text-slate-300 hover:text-white"}`;
  const viewer = !d.is_admin;
  const btNote = <Note>Backtest trades, not live trades. Net results are after fees.</Note>;
  const aNote = viewer ? btNote : (
    <Note>Real code: the site's own Elite 4 Edge code run on Binance BTCUSDT 5-minute candles until 31 Jan 2024 and Delta BTCUSD from 1 Feb 2024 (to signals before 1 Oct 2026), then on this server's Delta candles. These are backtest trades, not live trades.</Note>
  );
  return (
    <div className="space-y-4" data-testid="elite-page">
      <PageHead title="Elite 4 Edge" sub={viewer ? `Paper trading journal, net after fees. Updated ${ist(d.computed_at)}` : `One trade at a time for the whole account. $10,000 at 10x, stop 2 x ATR(14), Delta fees. Journals updated ${ist(d.computed_at)}${d.last_candle_ist ? ` · last 5m candle ${d.last_candle_ist}` : ""}`}
        right={<span className="flex items-center gap-2"><Chip status={MODE_CHIP[lv.mode]} label={`${MODE_NAME[lv.mode].toUpperCase()}`} testid="elite-mode" /><Chip status={lv.halt ? "BLOCKED" : "PASS"} label={lv.halt ? "STOPPED" : lv.open_trade ? "IN TRADE" : "WAITING FOR SIGNAL"} testid="elite-status" /></span>} />
      {d.error && <Note tone="red">{viewer ? "The latest update is delayed. It retries automatically." : `Last update failed: ${d.error}. It retries every 20 seconds.`}</Note>}
      {!viewer && <div className="grid grid-cols-1 gap-2 sm:grid-cols-2 lg:grid-cols-3" data-testid="elite-rules">
        {strategies.map((s) => (
          <div key={s.key} className="rounded-lg border border-line bg-panel2/60 px-3 py-2 text-[12px]">
            <div className="font-semibold text-slate-100">{s.priority}. {s.wb} <span className="font-normal text-slate-400">· {s.name}</span></div>
            <div className="text-slate-400">checked at each {s.tf} close · stop 2 x ATR(14) · time exit {hold(s.hold_h)}</div>
            <div className="text-slate-300">{s.rule}</div>
          </div>
        ))}
      </div>}
      {!viewer && <div className="text-[11.5px] text-slate-400">While a trade is open every new signal is skipped. Two signals on the same candle: the higher priority is taken. Market type = BTC now vs 30 days ago: Up above +5%, Down below −5%, otherwise Range.</div>}
      <div className="flex flex-wrap gap-2">
        <button className={btn(tab === "C")} onClick={() => setTab("C")} data-testid="elite-tab-C">C · Live paper trading</button>
        <button className={btn(tab === "A")} onClick={() => setTab("A")} data-testid="elite-tab-A">A · 1 Jan 2023 → today</button>
        <button className={btn(tab === "B")} onClick={() => setTab("B")} data-testid="elite-tab-B">B · 1 Oct 2026 → today</button>
      </div>
      {tab === "A" && <Journal j={d.A} title="1 Jan 2023 → today" strategies={strategies} note={aNote} viewer={viewer} />}
      {tab === "B" && <Journal j={d.B} title="1 Oct 2026 → today" strategies={strategies} note={viewer ? btNote : <Note>Same code on this server's Delta 5-minute candles from 1 Oct 2026. Backtest trades, not live trades.</Note>} viewer={viewer} />}
      {tab === "C" && <Live lv={lv} isAdmin={d.is_admin} reload={reload} />}
    </div>
  );
}
