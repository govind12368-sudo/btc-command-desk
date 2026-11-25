import React, { useEffect, useState } from "react";
import { toast } from "sonner";
import { useFetch } from "@/hooks/useFetch";
import { api, errText } from "@/lib/api";
import { Card, PageHead, Note, Chip } from "@/components/desk/ui";
import { Btn, Field, Input } from "@/components/desk/forms";
import { n, ist } from "@/lib/fmt";
import { PushCard } from "@/components/desk/Push";
import { SoundPicker } from "@/components/desk/SoundPicker";
import DeltaKeys from "@/components/desk/DeltaKeys";
import { DayFilterBar, FilterSummary, SupportLine, useDayFilter, useSupport, matches, isActive, LABEL_TYPE } from "@/components/desk/DayFilter";

const inr = (v) => (v == null ? "—" : `${v < 0 ? "−" : ""}₹${Math.round(Math.abs(v)).toLocaleString("en-IN")}`);
const money = (v) => (v == null ? "—" : `${v > 0 ? "+" : v < 0 ? "−" : ""}$${n(Math.abs(v), 2)}`);
const dur = (s) => {
  const t = Math.max(0, Math.floor(s));
  const h = Math.floor(t / 3600), m = Math.floor((t % 3600) / 60), x = t % 60;
  return h ? `${h}h ${String(m).padStart(2, "0")}m` : `${m}m ${String(x).padStart(2, "0")}s`;
};
const DIR = { LONG: "text-teal-300", SHORT: "text-red-300" };
const pnlTone = (v) => (v > 0 ? "text-teal-300" : v < 0 ? "text-red-300" : "text-slate-200");
const STATUS_CHIP = { Idle: "IDLE", Armed: "WAIT", "In trade": "PASS", Paused: "WAIT", "Limit hit": "BLOCKED" };
const MODE_CHIP = { PAPER: "IDLE", TESTNET: "WAIT", LIVE: "BLOCKED" };
const MODE_NAME = { PAPER: "Paper", TESTNET: "Testnet", LIVE: "Live" };
const REASON_TONE = { TARGET: "text-teal-300", STOP: "text-red-300", TIME: "text-amber-300", "DAILY LIMIT": "text-amber-300", MANUAL: "text-slate-300", SAFETY: "text-red-300", NEWS: "text-amber-300" };
const LEVEL_TONE = { error: "text-red-300", warn: "text-amber-300", info: "text-slate-300" };

const FIELDS = [
  ["margin_inr", "Capital per trade (₹ margin)", "Money put behind each trade; capped by your free Delta balance"],
  ["leverage", "Leverage (×)", "Position size = margin × leverage"],
  ["daily_loss_usd", "Daily loss limit ($)", "Trading stops for the day at this loss"],
  ["daily_target_usd", "Daily profit target ($)", "Trading stops for the day at this profit"],
  ["max_contracts", "Max contracts", "Hard cap. 1 contract = 0.001 BTC"],
  ["entry_window_min", "Entry window (minutes)", "How long a signal waits for its entry price"],
  ["slippage_pts", "Slippage guard (points)", "Skip when price is further than this past the entry"],
  ["time_exit_min", "Time exit (minutes)", "360 = 6 hours"],
  ["rr", "Reward to risk", "Target distance ÷ stop distance"],
  ["stop_mult", "Stop multiple", "1.0 = the signal's own stop distance"],
  ["usd_inr", "USD/INR rate", "Delta India settles at a fixed ₹85 per $"],
  ["buffer_inr", "Balance kept free (₹)", "Never used as margin"],
  ["paper_balance_inr", "Paper starting balance (₹)", "Paper mode only"],
  ["tp_limit", "Take-profit order (0 or 1)", "0 = market order at target, 1 = limit order at target"],
  ["stop_slip_pct", "Stop slippage allowance (% of price)", "Each trade is sized as if its stop fills this much worse. 0 = off"],
  ["news_before_min", "No new trade before big news (minutes)", "US CPI, jobs report, FOMC, GDP, PCE … 0 = off"],
  ["news_after_min", "No new trade after big news (minutes)", "Counted from the release time. 0 = off"],
  ["news_close_min", "Close open trade before big news (minutes)", "0 = keep the trade open through the news"],
  ["slip_pause_avg_pts", "Pause when average slippage is above (points)", "Average of the last 10 trades (needs 5). 0 = off"],
];

const pts = (v) => (v == null ? "—" : `${v > 0 ? "+" : v < 0 ? "−" : ""}${n(Math.abs(v), 1)}`);
const slipTone = (v) => (v > 0.05 ? "text-red-300" : v < -0.05 ? "text-teal-300" : "text-slate-200");

function SlipCard({ d }) {
  const cfg = d.settings, nw = d.news || {}, s = d.slippage || {};
  const allowPts = d.price ? (d.price * cfg.stop_slip_pct) / 100 : null;
  const room = allowPts != null ? allowPts * 0.001 : null;
  const newsOn = cfg.news_before_min > 0 || cfg.news_after_min > 0;
  return (
    <Card title="Slippage protection" sub="Slippage = the gap between the price a trade should fill at and the price it really filled at. Positive points = worse for you." testid="autotrade-slippage">
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
        <div data-testid="autotrade-slip-sizing">
          <div className="mb-1 text-[12px] font-semibold text-slate-200">1. Sized for a bad stop fill</div>
          {cfg.stop_slip_pct > 0 ? (
            <p className="text-[12px] text-slate-300">
              Every trade is sized as if its stop fills <span className="font-mono text-slate-50">{n(cfg.stop_slip_pct, 2)}%</span> worse than the stop
              {allowPts != null && <> (about <span className="font-mono text-slate-50">{n(allowPts, 0)}</span> points now, ${n(room, 2)} per contract)</>}.
              A bad fill of that size still stays inside the daily loss limit of ${n(cfg.daily_loss_usd, 0)}.
            </p>
          ) : <p className="text-[12px] text-amber-300">Off. Trades are sized as if the stop always fills exactly.</p>}
        </div>
        <div data-testid="autotrade-slip-news">
          <div className="mb-1 text-[12px] font-semibold text-slate-200">2. Big US news</div>
          {!newsOn ? <p className="text-[12px] text-amber-300">Off. Trades are taken and held through big news.</p> : (
            <>
              {nw.block
                ? <p className="text-[12px] text-amber-300">No new trades now: {nw.block.title} at {nw.block.time_ist}.</p>
                : <p className="text-[12px] text-slate-300">New trades are allowed now.</p>}
              <p className="mt-1 text-[12px] text-slate-300">
                {nw.next ? <>Next: <span className="text-slate-50">{nw.next.title}</span> at <span className="font-mono">{nw.next.time_ist}</span> <span className="text-slate-500">({nw.next.publisher})</span></> : nw.calendar_ok ? "No high-impact US release in the calendar's next 45 days." : "Next release: not known yet."}
              </p>
              <p className="mt-1 text-[11.5px] text-slate-400">
                No new trade from {n(cfg.news_before_min, 0)} min before to {n(cfg.news_after_min, 0)} min after.{" "}
                {cfg.news_close_min > 0 ? `An open trade is closed ${n(cfg.news_close_min, 0)} min before.` : "An open trade is kept open."}
              </p>
              {!nw.calendar_ok && <p className="mt-1 text-[11.5px] text-amber-300">The official calendar (BLS, BEA, Fed) is not fully loaded right now, so some releases may be missing. Events you add on News & Events still count.</p>}
            </>
          )}
        </div>
        <div data-testid="autotrade-slip-record">
          <div className="mb-1 text-[12px] font-semibold text-slate-200">3. Slippage record ({MODE_NAME[d.mode]} mode)</div>
          {s.trades ? (
            <>
              <div className="grid grid-cols-2 gap-2">
                <Fig label="Average per trade" value={`${pts(s.avg_pts)} pts`} sub={`entry ${pts(s.avg_entry_pts)} · exit ${pts(s.avg_exit_pts)}`} tone={slipTone(s.avg_pts)} />
                <Fig label="Stop exits" value={s.stop_exits ? `${pts(s.avg_stop_pts)} pts` : "—"} sub={`${s.stop_exits} stop${s.stop_exits === 1 ? "" : "s"}`} tone={slipTone(s.avg_stop_pts)} />
                <Fig label="Cost of slippage" value={money(-s.total_usd)} sub={`${s.trades} trade${s.trades === 1 ? "" : "s"}`} tone={pnlTone(-s.total_usd)} />
                <Fig label="Worst trade" value={`${pts(s.worst?.pts)} pts`} sub={s.worst?.signal_id} tone={slipTone(s.worst?.pts)} />
              </div>
              <p className="mt-2 text-[11.5px] text-slate-400">
                Last {s.recent_n} trade{s.recent_n === 1 ? "" : "s"} since the last resume: {s.recent_n ? `${pts(s.recent_avg_pts)} points on average` : "none yet"}
                {s.pause_avg_pts > 0 ? `; pauses above ${n(s.pause_avg_pts, 0)} (needs ${s.min_trades} trades).` : "; average brake off."}
              </p>
            </>
          ) : <p className="text-[12px] text-slate-400">No closed trade with a slippage record yet. Each new trade stores the price expected and the price got.</p>}
          <p className="mt-1 text-[11.5px] text-slate-400">Auto-Trade pauses itself if one trade slips more than the sizing allowance{s.pause_avg_pts > 0 ? ", or the average goes above the limit" : ""}.</p>
        </div>
      </div>
    </Card>
  );
}

function useNow() {
  const [t, setT] = useState(Date.now());
  useEffect(() => { const i = setInterval(() => setT(Date.now()), 1000); return () => clearInterval(i); }, []);
  return t;
}

function PnlMeter({ today }) {
  const { total, loss_limit: loss, target, usd_inr: rate } = today;
  const span = loss + target;
  const zero = (loss / span) * 100;
  const pos = Math.max(0, Math.min(100, ((total + loss) / span) * 100));
  const used = total < 0 ? Math.min(100, (-total / loss) * 100) : 0;
  const reached = total > 0 ? Math.min(100, (total / target) * 100) : 0;
  return (
    <div data-testid="autotrade-meter">
      <div className="relative mt-1 h-3.5 rounded border border-line bg-panel2" role="img"
        aria-label={`Today's profit and loss ${money(total)} between a loss limit of ${loss} dollars and a target of ${target} dollars`}>
        <div className={`absolute top-0 h-full rounded-sm ${total >= 0 ? "bg-teal-400" : "bg-red-400"}`} style={{ left: `${Math.min(zero, pos)}%`, width: `${Math.abs(pos - zero)}%` }} />
        <div className="absolute -top-1 h-[22px] w-px bg-slate-300" style={{ left: `${zero}%` }} />
      </div>
      <div className="relative mt-1.5 h-9 text-[11px] text-slate-400">
        <span className="absolute left-0">Stop at −${n(loss, 0)}<br />{inr(-loss * rate)}</span>
        <span className="absolute -translate-x-1/2" style={{ left: `${zero}%` }}>0</span>
        <span className="absolute right-0 text-right">Stop at +${n(target, 0)}<br />{inr(target * rate)}</span>
      </div>
      <div className="mt-1 grid grid-cols-2 gap-2 text-[11.5px] text-slate-400">
        <div>Loss limit used <span className="font-mono text-slate-200">{n(used, 0)}%</span></div>
        <div className="text-right">Target reached <span className="font-mono text-slate-200">{n(reached, 0)}%</span></div>
      </div>
    </div>
  );
}

function Fig({ label, value, sub, tone = "text-slate-50" }) {
  return (
    <div className="rounded-lg border border-line bg-panel2/60 px-3 py-2">
      <div className="text-[11.5px] text-slate-400">{label}</div>
      <div className={`font-mono text-base font-bold ${tone}`}>{value}</div>
      {sub && <div className="font-mono text-[11px] text-slate-400">{sub}</div>}
    </div>
  );
}

function SettingsForm({ d, reload }) {
  const [form, setForm] = useState({});
  const [busy, setBusy] = useState(false);
  const dirty = Object.keys(form).length > 0;
  const save = async () => {
    setBusy(true);
    try {
      await api.put("/autotrade/settings", { values: form });
      setForm({}); reload();
      toast.success("Settings saved. They apply from the next price update.");
    } catch (e) { toast.error(errText(e)); } finally { setBusy(false); }
  };
  return (
    <Card title="Settings" sub="Changes apply immediately, including to today's limits. An open trade keeps the stop and target it was opened with." testid="autotrade-settings">
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-4">
        {FIELDS.map(([k, label, hint]) => (
          <Field key={k} label={label} hint={`${hint} · allowed ${d.ranges[k][0]}–${d.ranges[k][1].toLocaleString("en-US")}`}>
            <Input type="number" step="any" inputMode="decimal" data-testid={`autotrade-set-${k}`} value={k in form ? form[k] : d.settings[k]}
              onChange={(e) => setForm({ ...form, [k]: e.target.value })} />
          </Field>
        ))}
      </div>
      <div className="mt-3 flex flex-wrap items-center gap-3">
        <Btn onClick={save} disabled={!dirty || busy} data-testid="autotrade-save">Save settings</Btn>
        {dirty && <Btn tone="ghost" onClick={() => setForm({})}>Undo changes</Btn>}
        <span className="text-[11.5px] text-slate-400">
          Daily limits now: loss ${n(d.settings.daily_loss_usd, 0)} ({inr(d.settings.daily_loss_usd * d.settings.usd_inr)}) · target ${n(d.settings.daily_target_usd, 0)} ({inr(d.settings.daily_target_usd * d.settings.usd_inr)})
        </span>
      </div>
    </Card>
  );
}

function Pushover() {
  const { data, reload } = useFetch("/alerts/channels", 0);
  const [form, setForm] = useState({});
  const f = data?.fields || {};
  const set = f.PUSHOVER_USER_KEY?.set && f.PUSHOVER_APP_TOKEN?.set;
  const save = async () => {
    try { await api.put("/alerts/channels", { values: form }); setForm({}); reload(); toast.success("Pushover saved"); } catch (e) { toast.error(errText(e)); }
  };
  return (
    <Card title="Alerts" sub="Entry, exit, limit hit and errors go to the alert channels already set up in Settings (email, Telegram). Pushover is optional and is a paid phone app." testid="autotrade-alerts">
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
        <Field label="Pushover user key" hint={f.PUSHOVER_USER_KEY?.set ? "Saved. Leave empty to keep it." : "Not set"}>
          <Input type="password" autoComplete="off" value={form.PUSHOVER_USER_KEY || ""} onChange={(e) => setForm({ ...form, PUSHOVER_USER_KEY: e.target.value })} />
        </Field>
        <Field label="Pushover app token" hint={f.PUSHOVER_APP_TOKEN?.set ? "Saved. Leave empty to keep it." : "Not set"}>
          <Input type="password" autoComplete="off" value={form.PUSHOVER_APP_TOKEN || ""} onChange={(e) => setForm({ ...form, PUSHOVER_APP_TOKEN: e.target.value })} />
        </Field>
        <div className="flex items-end gap-2">
          <Btn onClick={save} disabled={!Object.keys(form).length}>Save Pushover</Btn>
          <span className="pb-1.5 text-[11.5px] text-slate-400">{set ? "Pushover is on" : "Pushover is off"}</span>
        </div>
      </div>
    </Card>
  );
}

export default function AutoTrade() {
  const { data: d, reload } = useFetch("/autotrade", 2000);
  const now = useNow();
  const [busy, setBusy] = useState(false);
  const [confirmClose, setConfirmClose] = useState(false);
  const [liveBox, setLiveBox] = useState(false);
  const [word, setWord] = useState("");
  const [skew, setSkew] = useState(0);
  useEffect(() => { if (d?.server_time) setSkew(Date.parse(d.server_time) - Date.now()); }, [d?.server_time]);
  const [f, setF] = useDayFilter("autotrade");
  const sup = useSupport();
  if (!d) return <div className="text-sm text-slate-400">Loading auto-trade…</div>;
  const journal = d.journal.filter((r) => matches(f, LABEL_TYPE[r.day_type], r.plan, sup[r.signal_id]?.level));

  const act = async (fn, ok) => {
    setBusy(true);
    try { await fn(); toast.success(ok); await reload(); } catch (e) { toast.error(errText(e)); } finally { setBusy(false); }
  };
  const control = (action, ok) => act(() => api.post("/autotrade/control", { action }), ok);
  const setMode = (mode, confirm) => act(() => api.post("/autotrade/mode", { mode, confirm }), `Mode is now ${MODE_NAME[mode]}`).then(() => { setLiveBox(false); setWord(""); });
  const exportCsv = async () => {
    try {
      const r = await api.get("/autotrade/journal.csv", { responseType: "blob" });
      const u = URL.createObjectURL(r.data);
      const a = document.createElement("a");
      a.href = u; a.download = "my-btc-partner-autotrade-journal.csv"; a.click();
      URL.revokeObjectURL(u);
    } catch (e) { toast.error(errText(e)); }
  };

  const t = d.open_trade;
  const rate = d.today.usd_inr;
  const serverNow = now + skew;
  const lr = d.live_ready;
  const yes = (b) => <span className={b ? "text-teal-300" : "text-slate-400"}>{b ? "Done" : "Not yet"}</span>;

  return (
    <div className="space-y-4" data-testid="autotrade-page">
      <PageHead title="Auto-trade" sub={d.source === "strategies" ? "Takes only Flush Short and Candle Runs trades (live positions from those pages), one trade at a time. Flush Short: SHORT at the signal entry, 1.25R target, 6-hour exit. Candle Runs: market entry within 30 minutes of the 4h signal, shared stop, target = the largest target of the strategies that signalled, 7-day exit." : `Scalp signals only, one trade at a time, 6-hour exit. Direction: ${["Plan A (reverse 1:2) always", "follow the signal always", "Day-type switch only – range day → Plan A, trend day with the signal → follow, anything else → no trade"][d.settings.direction_mode] || ""}.`}
        right={<span className="flex items-center gap-2"><Chip status={MODE_CHIP[d.mode]} label={`${MODE_NAME[d.mode].toUpperCase()} MODE`} testid="autotrade-mode" /><Chip status={STATUS_CHIP[d.status]} label={d.status.toUpperCase()} testid="autotrade-status" /></span>} />

      {d.mode === "PAPER" && <Note>Paper mode: trades are simulated from the live Delta price with the same rules, sizing and fees. No order is sent to the exchange.</Note>}
      {d.mode === "LIVE" && <Note tone="red">Live mode: real orders are placed on your Delta Exchange account.</Note>}
      {d.halt && <Note tone="amber">{d.halt.reason} ({money(d.halt.pnl)} at {ist(d.halt.at)}). No new trades until 00:00 IST.</Note>}
      {d.paused && !d.halt && <Note tone="amber">{d.pause_reason ? `New trades are paused. ${d.pause_reason}. Check the fills in the journal, then press Resume.` : "New trades are paused. Press Resume to continue."}</Note>}

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <Card title="Today" sub={`Trading day ${d.today.day} (00:00–23:59 IST) · ${MODE_NAME[d.mode]} mode`} testid="autotrade-today">
          <div className="grid grid-cols-3 gap-2">
            <Fig label="Today's P&L" value={money(d.today.total)} sub={inr(d.today.total * rate)} tone={pnlTone(d.today.total)} />
            <Fig label="Closed trades" value={money(d.today.realized)} sub={inr(d.today.realized * rate)} tone={pnlTone(d.today.realized)} />
            <Fig label="Open trade" value={money(d.today.unrealized)} sub="after fees" tone={pnlTone(d.today.unrealized)} />
          </div>
          <div className="mt-3"><PnlMeter today={d.today} /></div>
        </Card>

        <Card title="Controls" sub={`BTC last price ${d.price ? n(d.price, 1) : "—"}${d.paper_balance_inr != null ? ` · paper balance ${inr(d.paper_balance_inr)}` : ""}`} testid="autotrade-controls">
          <div className="flex flex-wrap gap-2">
            {d.paused
              ? <Btn tone="teal" disabled={busy} onClick={() => control("resume", "Resumed")} data-testid="autotrade-resume">Resume</Btn>
              : <Btn tone="ghost" disabled={busy} onClick={() => control("pause", "New trades paused")} data-testid="autotrade-pause">Pause new trades</Btn>}
            {confirmClose
              ? <><Btn tone="red" disabled={busy} onClick={() => control("close_all", "Everything closed. Auto-trade is paused.").then(() => setConfirmClose(false))} data-testid="autotrade-close-confirm">Yes, close and pause</Btn>
                <Btn tone="ghost" onClick={() => setConfirmClose(false)}>Keep running</Btn></>
              : <Btn tone="red" disabled={busy} onClick={() => setConfirmClose(true)} data-testid="autotrade-close">Close everything now</Btn>}
          </div>
          <p className="mt-2 text-[11.5px] text-slate-400">Pause keeps an open trade under its stop and target. Close everything sells the position at market, cancels all orders and pauses.</p>
          <div className="mt-4 border-t border-line pt-3">
            <div className="mb-2 text-[12px] font-semibold text-slate-200">Mode</div>
            <div className="flex flex-wrap gap-2">
              {["PAPER", "TESTNET", "LIVE"].map((m) => (
                <Btn key={m} tone={d.mode === m ? "gold" : "ghost"} disabled={busy || d.mode === m} data-testid={`autotrade-mode-${m}`}
                  onClick={() => (m === "LIVE" ? setLiveBox(true) : setMode(m))}>{MODE_NAME[m]}{d.mode === m ? " (current)" : ""}</Btn>
              ))}
            </div>
            {liveBox && (
              <div className="mt-3 flex flex-wrap items-end gap-2 rounded border border-red-500/30 bg-red-500/5 p-3">
                <Field label="Type LIVE to place real orders" hint="Real money. Only works after the checks below are done.">
                  <Input value={word} onChange={(e) => setWord(e.target.value)} placeholder="LIVE" autoComplete="off" data-testid="autotrade-live-word" />
                </Field>
                <Btn tone="red" disabled={busy || word !== "LIVE"} onClick={() => setMode("LIVE", word)}>Switch to Live</Btn>
                <Btn tone="ghost" onClick={() => { setLiveBox(false); setWord(""); }}>Cancel</Btn>
              </div>
            )}
            <ul className="mt-3 space-y-1 text-[11.5px] text-slate-400" data-testid="autotrade-checklist">
              <li>1. Testnet key connected (Delta Exchange connection card): {yes(lr.testnet_key)}</li>
              <li>2. One completed Testnet trade (entry, stop and target confirmed, exit): {yes(lr.testnet_trade_done)}</li>
              <li>3. Live key connected (Read Data and Trading only, server IP whitelisted): {yes(lr.live_key)}</li>
            </ul>
          </div>
        </Card>
      </div>

      <SlipCard d={d} />

      <DeltaKeys />

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <Card title="Open trade" sub={t ? `${t.signal_id} · filled ${ist(t.fill_time)}` : "No trade is open"} testid="autotrade-open">
          {t ? (
            <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
              <Fig label="Direction" value={t.bot_dir} sub={`${t.plan === "FOLLOW" ? "following" : "Plan A, reverse of"} ${t.model_dir} signal${t.day_type ? ` · ${t.day_type}` : ""}`} tone={DIR[t.bot_dir]} />
              <Fig label="Entry" value={n(t.entry, 1)} sub={`planned ${n(t.trigger_entry, 1)}`} />
              <Fig label="Stop" value={n(t.stop, 1)} tone="text-red-300" />
              <Fig label="Target" value={n(t.target, 1)} tone="text-teal-300" />
              <Fig label="Contracts" value={t.contracts} sub={`${n(t.contracts * 0.001, 3)} BTC`} />
              <Fig label="Margin used" value={`$${n(t.margin_usd, 2)}`} sub={inr(t.margin_usd * rate)} />
              <Fig label="Unrealized P&L" value={money(t.unrealized)} sub={inr(t.unrealized * rate)} tone={pnlTone(t.unrealized)} />
              <Fig label="6-hour exit in" value={dur(t.time_left_sec - (serverNow - Date.parse(d.server_time)) / 1000)} sub={t.bracket_confirmed ? "stop + target confirmed" : "checking stop + target"} />
            </div>
          ) : <p className="py-4 text-center text-sm text-slate-500">Waiting for the next scalp signal.</p>}
        </Card>

        <Card title="Armed signals" sub="Waiting for price to reach the entry. The first one to trigger is traded; the others are cancelled." testid="autotrade-armed">
          {d.armed.length ? (
            <div className="overflow-x-auto scroll-thin">
              <table className="desk-table">
                <thead><tr><th>Signal</th><th>Method</th><th>Bot</th><th>Entry</th><th>Stop</th><th>Target</th><th>Expires in</th></tr></thead>
                <tbody>
                  {d.armed.map((a) => (
                    <tr key={a._id}>
                      <td className="font-mono text-[11.5px]">{a._id}</td>
                      <td className="text-[11.5px]">{a.plan === "FOLLOW" ? "Follow" : "Plan A"}</td>
                      <td className={DIR[a.bot_dir]}>{a.bot_dir}</td>
                      <td className="font-mono">{n(a.entry, 1)}</td>
                      <td className="font-mono">{n(a.stop, 1)}</td>
                      <td className="font-mono">{n(a.target, 1)}</td>
                      <td className="font-mono">{dur((Date.parse(a.window_end) - serverNow) / 1000)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : <p className="py-4 text-center text-sm text-slate-500">Nothing armed right now.</p>}
        </Card>
      </div>

      <Card title="Trade journal" sub="Closed auto-trades, newest first. Fees: taker 0.05%, maker 0.02%, plus 18% GST." testid="autotrade-journal"
        right={<Btn tone="ghost" onClick={exportCsv} data-testid="autotrade-csv">Download CSV</Btn>}>
        <DayFilterBar f={f} setF={setF} stoodAside={false} testid="autotrade-filter" />
        <FilterSummary f={f} setF={setF} shown={journal.length} total={d.journal.length} what="closed trades" trades={journal.length} wins={journal.filter((r) => r.net_usd > 0).length} net={journal.reduce((a, r) => a + (r.net_usd || 0), 0)} basis="Auto-Trade's own fills" />
        <div className="overflow-x-auto scroll-thin">
          <table className="desk-table">
            <thead><tr><th>Signal</th><th>Mode</th><th>Method and day type</th><th>Signal dir</th><th>Bot dir</th><th>Captured</th><th>Filled</th><th>Entry</th><th>Stop</th><th>Target</th><th>Contracts</th><th>Exit time</th><th>Exit reason</th><th>Slippage (pts)</th><th>Gross $</th><th>Fees $</th><th>Net $</th><th>Net ₹</th></tr></thead>
            <tbody>
              {journal.map((r) => (
                <tr key={r.signal_id}>
                  <td className="font-mono text-[11.5px]">{r.signal_id}</td>
                  <td>{MODE_NAME[r.mode]}</td>
                  <td className="text-[11.5px]">{r.plan === "FOLLOW" ? "Follow" : "Plan A"}{r.day_type ? <span className="block text-slate-500">{r.day_type}</span> : null}<SupportLine sup={sup[r.signal_id]} playbook={r.plan || "PLAN_A"} /></td>
                  <td className={DIR[r.model_dir]}>{r.model_dir}</td>
                  <td className={DIR[r.bot_dir]}>{r.bot_dir}</td>
                  <td className="font-mono text-[11.5px]">{ist(r.captured_at)}</td>
                  <td className="font-mono text-[11.5px]">{ist(r.fill_time)}</td>
                  <td className="font-mono">{n(r.entry, 1)}</td>
                  <td className="font-mono">{n(r.stop, 1)}</td>
                  <td className="font-mono">{n(r.target, 1)}</td>
                  <td className="font-mono">{r.contracts}</td>
                  <td className="font-mono text-[11.5px]">{ist(r.exit_time)}</td>
                  <td className={`font-semibold ${REASON_TONE[r.exit_reason] || "text-slate-300"}`}>{r.exit_reason}</td>
                  <td className="font-mono">{r.slip_pts == null ? "—" : <><span className={slipTone(r.slip_pts)}>{pts(r.slip_pts)}</span><span className="block text-[10.5px] text-slate-500">in {pts(r.entry_slip_pts)} · out {pts(r.exit_slip_pts)}</span></>}</td>
                  <td className="font-mono">{n(r.gross, 2)}</td>
                  <td className="font-mono">{n(r.fees, 2)}</td>
                  <td className={`font-mono ${pnlTone(r.net_usd)}`}>{money(r.net_usd)}</td>
                  <td className={`font-mono ${pnlTone(r.net_inr)}`}>{inr(r.net_inr)}</td>
                </tr>
              ))}
              {!journal.length && <tr><td colSpan={18} className="py-6 text-center text-slate-500">{isActive(f) ? "No closed trade matches this filter." : "No closed trades yet. They appear here as soon as a trade exits."}</td></tr>}
            </tbody>
          </table>
        </div>
      </Card>

      <Card title="Event log" sub="Every decision: armed, not filled, skipped and why, entered, stop and target confirmed, exited, limit hit, errors." testid="autotrade-events">
        <div className="max-h-80 space-y-1 overflow-y-auto scroll-thin">
          {d.events.map((e, i) => (
            <div key={`${e.at}-${i}`} className="flex gap-3 text-[12px]">
              <span className="shrink-0 font-mono text-[11px] text-slate-500">{ist(e.at)}</span>
              <span className={LEVEL_TONE[e.level] || "text-slate-300"}>{e.text}</span>
            </div>
          ))}
          {!d.events.length && <p className="py-4 text-center text-sm text-slate-500">Nothing has happened yet. New scalp signals show up here.</p>}
        </div>
      </Card>

      <PushCard />
      <SoundPicker />
      <SettingsForm d={d} reload={reload} />
      <Pushover />
    </div>
  );
}
