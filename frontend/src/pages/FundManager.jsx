import React, { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { toast } from "sonner";
import { api, errText } from "@/lib/api";
import { useFetch } from "@/hooks/useFetch";
import { Card, Chip, PageHead, Note, More, KpiStrip, Tabs, Empty, usePaged } from "@/components/desk/ui";
import { Field, Input, Btn } from "@/components/desk/forms";
import { LineChart, MonthBars, VIZ } from "@/components/desk/charts";
import { n, signedUsd, ist, ago } from "@/lib/fmt";

const tone = (v) => (v > 0 ? "text-teal-300" : v < 0 ? "text-red-300" : "text-slate-300");
const usd0 = (v) => (v == null ? "—" : `${v < 0 ? "−" : ""}$${n(Math.abs(v), 0)}`);
const pc = (v, d = 1) => (v == null ? "—" : `${v > 0 ? "+" : v < 0 ? "−" : ""}${Math.abs(v).toFixed(d)}%`);
const NAMES = { elite: "Elite 4 Edge", optpicks: "Option Picks", flush: "Flush Short" };
const GROUPS = [["elite", "Elite 4 Edge"], ["optpicks", "Option Picks · pick 5"], ["flush", "Flush Short v3"], ["server", "Server and data feed"]];

/* -------- this month: loss limit … 0 … target */
function MonthBar({ m }) {
  const span = m.loss_limit + m.target || 1;
  const zero = (m.loss_limit / span) * 100;
  const pos = Math.max(0, Math.min(100, ((m.net + m.loss_limit) / span) * 100));
  return (
    <div data-testid="fund-month-bar">
      <div className="relative h-2.5 rounded-sm border border-line bg-panel2">
        <div className={`absolute inset-y-0 ${m.net >= 0 ? "bg-teal-500/70" : "bg-red-500/70"}`} style={{ left: `${Math.min(zero, pos)}%`, width: `${Math.abs(pos - zero)}%` }} />
        <div className="absolute -inset-y-1 w-px bg-slate-300" style={{ left: `${zero}%` }} />
      </div>
      <div className="mt-1 flex justify-between font-mono text-[10.5px] text-slate-500">
        <span>month stop {usd0(-m.loss_limit)}</span><span>{m.key} · {signedUsd(m.net)} ({pc(m.pct, 2)})</span><span>target {usd0(m.target)}</span>
      </div>
    </div>
  );
}

/* -------- money in use, by strategy, against the fund */
function UseBar({ d }) {
  const cap = d.fund.capital || 1;
  const col = { elite: VIZ.s1, optpicks: VIZ.s2, flush: VIZ.s3 };
  return (
    <div data-testid="fund-use-bar">
      <div className="relative flex h-2.5 overflow-hidden rounded-sm border border-line bg-panel2">
        {d.strategies.filter((s) => s.in_use > 0).map((s) => <div key={s.key} title={`${s.name}: $${n(s.in_use, 0)}`} style={{ width: `${(s.in_use / cap) * 100}%`, background: col[s.key], marginRight: 2 }} />)}
        <div className="absolute -inset-y-1 w-px bg-amber-400" style={{ left: `${d.fund.max_use_pct}%` }} title={`utilisation cap ${d.fund.max_use_pct}%`} />
      </div>
      <div className="mt-1 flex flex-wrap justify-between gap-x-4 font-mono text-[10.5px] text-slate-500">
        <span>in use {usd0(d.fund.in_use)} ({n(d.fund.in_use_pct, 1)}%)</span>
        <span className="flex flex-wrap gap-x-3">{d.strategies.map((s) => <span key={s.key}><span style={{ color: col[s.key] }}>■</span> {NAMES[s.key]}</span>)}</span>
        <span>cap {d.fund.max_use_pct}% · free {usd0(d.fund.free)}</span>
      </div>
    </div>
  );
}

function Overview({ d, act, busy }) {
  const f = d.fund;
  return (
    <div className="space-y-3">
      <Card title="This month" sub={`Target ${f.month.target_pct}% of the fund · ${f.month.days_left} days left`} testid="fund-month">
        <MonthBar m={f.month} />
      </Card>
      <Card title="Decisions now" sub="What the fund is doing with each strategy, from its rules" testid="fund-decisions" pad={false}>
        <div className="divide-y divide-line/60">
          {d.decisions.map((x) => (
            <div key={x.key} className="flex items-start gap-3 px-3 py-1.5">
              <span className="w-[92px] shrink-0 pt-0.5"><Chip status={x.status} /></span>
              <span className="min-w-0 text-[12.5px] leading-relaxed text-slate-200">{x.text}</span>
            </div>
          ))}
        </div>
      </Card>
      <Card title="Allocation and money in use" sub="Size = the strategy's capital ÷ the capital one page-size trade needs" testid="fund-allocation" pad={false}>
        <div className="overflow-x-auto scroll-thin">
          <table className="desk-table">
            <thead><tr><th>Strategy</th><th className="num">Share</th><th className="num">Capital</th><th className="num">Size</th><th className="num">Position</th><th className="num">Margin in use</th>
              <th className="num">Open P&L</th><th className="num">This month</th><th className="num">Since start</th><th className="num">From peak / stop</th><th>Status</th>{d.is_admin && <th />}</tr></thead>
            <tbody>
              {d.strategies.map((s) => (
                <tr key={s.key}>
                  <td><Link to={s.to} className="font-semibold text-slate-100 hover:text-amber-300">{s.name}</Link><div className="text-[11px] text-slate-500">{s.what}</div></td>
                  <td className="num">{n(s.alloc_pct, 0)}%</td><td className="num">{usd0(s.capital)}</td><td className="num">{n(s.k, 2)}×</td><td className="num">{usd0(s.position)}</td>
                  <td className="num">{usd0(s.in_use)}</td><td className={`num ${tone(s.open_net)}`}>{signedUsd(s.open_net)}</td><td className={`num ${tone(s.month_net)}`}>{signedUsd(s.month_net)}</td>
                  <td className={`num font-semibold ${tone(s.net)}`}>{signedUsd(s.net)}</td><td className="num">{usd0(s.dd)} / {usd0(-s.dd_limit)}</td>
                  <td><Chip status={s.paused ? "PAUSED" : s.capital <= 0 ? "OFF" : s.open_trades ? "IN TRADE" : "ACTIVE"} /></td>
                  {d.is_admin && <td className="text-right">{s.paused
                    ? <Btn tone="teal" disabled={busy} onClick={() => act({ action: "resume", strategy: s.key }, `${s.name} resumed`)} data-testid={`fund-resume-${s.key}`}>Resume</Btn>
                    : <Btn tone="ghost" disabled={busy || s.capital <= 0} onClick={() => act({ action: "pause", strategy: s.key }, `${s.name} paused`)} data-testid={`fund-pause-${s.key}`}>Pause</Btn>}</td>}
                </tr>
              ))}
              <tr className="border-t-2 border-line2 font-semibold">
                <td>Fund<div className="text-[11px] font-normal text-slate-500">cash reserve {n(f.reserve_pct, 0)}%</div></td><td className="num">{n(100 - f.reserve_pct, 0)}%</td><td className="num">{usd0(f.capital)}</td><td /><td />
                <td className="num">{usd0(f.in_use)}</td><td className={`num ${tone(f.open)}`}>{signedUsd(f.open)}</td><td className={`num ${tone(f.month.net)}`}>{signedUsd(f.month.net)}</td>
                <td className={`num ${tone(f.closed)}`}>{signedUsd(f.closed)}</td><td className="num">{usd0(f.dd)} / {usd0(-f.dd_limit)}</td>
                <td><Chip status={f.stop_all ? "STOPPED" : f.month.stopped ? "STOPPED" : "ACTIVE"} /></td>{d.is_admin && <td />}
              </tr>
            </tbody>
          </table>
        </div>
        <div className="border-t border-line px-3 py-2"><UseBar d={d} /></div>
      </Card>
      {d.is_admin && d.exchange && !d.exchange.error && (
        <Card title="Delta account" sub="Read-only balance check. The paper fund does not use this money." testid="fund-exchange">
          <div className="grid gap-2 sm:grid-cols-2">
            {["TESTNET", "LIVE"].map((m) => { const x = d.exchange[m] || {}; return (
              <div key={m} className="flex items-center justify-between gap-3 rounded-sm border border-line bg-panel2 px-2.5 py-1.5">
                <div><div className="font-mono text-[10.5px] text-slate-500">{m === "LIVE" ? "Live key (real money)" : "Testnet key (practice)"}</div>
                  <div className="font-mono text-[14px] text-slate-100">{x.connected ? (x.balance_inr != null ? `₹${n(x.balance_inr, 0)} ≈ $${n(x.balance_usd, 0)}` : "connected") : "not connected"}</div>
                  {x.error && <div className="text-[11.5px] text-red-300">{x.error}</div>}</div>
                <span className="font-mono text-[10.5px] text-slate-500">{x.connected && x.checked_at ? `checked ${ago(x.checked_at)}` : ""}</span>
              </div>); })}
          </div>
        </Card>
      )}
    </div>
  );
}

function Checks({ integ }) {
  if (!integ) return <Empty>The first signal check runs a few minutes after a restart.</Empty>;
  return (
    <div className="space-y-3" data-testid="fund-checks">
      <Note tone="plain">Checked again every 4 minutes on this server: candle data without gaps, every candle close scanned, live signals against the rules run again, paper results against the backtest. Last check {ago(integ.summary?.at)}.</Note>
      {GROUPS.map(([g, title]) => (
        <Card key={g} title={title} right={<span className="font-mono text-[11px] text-slate-400">{(integ[g] || []).filter((c) => c.status === "OK").length} of {(integ[g] || []).length} pass</span>} pad={false} testid={`fund-checks-${g}`}>
          <table className="desk-table">
            <tbody>{(integ[g] || []).map((c) => (
              <tr key={c.name} title={c.more || ""}><td className="w-[250px] text-slate-200">{c.name}</td><td className="w-[72px]"><Chip status={c.status === "WAIT" ? "PENDING" : c.status} label={c.status === "WAIT" ? "WAITING" : undefined} /></td>
                <td className="whitespace-normal text-[12px] text-slate-300">{c.detail}{c.more && <div className="text-[11px] text-slate-500">{c.more}</div>}</td></tr>))}
            </tbody>
          </table>
        </Card>
      ))}
    </div>
  );
}

function History({ h }) {
  const [view, setView] = useState("rules");
  if (!h) return <Empty>The history table is worked out a few minutes after a restart.</Empty>;
  const v = h.views[view], s = v.summary;
  if (!s) return <Empty>No backtest months yet.</Empty>;
  return (
    <div className="space-y-3" data-testid="fund-history">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <Tabs testid="fund-view" value={view} onChange={setView} items={[["rules", "With the fund's rules"], ["plain", "Every trade, no rules"], ["stress", "Worse option fills"]]} />
        <span className="font-mono text-[11px] text-slate-500">backtest {h.from} → {h.to} · {s.months} months · not live results</span>
      </div>
      <KpiStrip testid="fund-history-kpi" items={[
        { label: "Average month", value: pc(s.avg_pct), cls: tone(s.avg_pct), sub: `median ${pc(s.median_pct)}` },
        { label: `Reached ${h.target_pct}%`, value: `${s.hit} of ${s.months}`, sub: `last 12 months: ${s.last12_hit} of ${s.last12_n}` },
        { label: "Losing months", value: `${s.negative} of ${s.months}`, cls: s.negative ? "text-red-300" : "text-slate-50" },
        { label: "Worst month", value: pc(s.worst_pct), cls: "text-red-300", sub: s.worst_month },
        { label: "Deepest fall", value: pc(s.max_dd_pct), cls: "text-red-300", sub: `${usd0(s.max_dd)} from a peak, closed trades` },
        { label: "Last 12 months", value: pc(s.last12_avg_pct), cls: tone(s.last12_avg_pct), sub: "average month" },
      ]} />
      <Card title="Return by month" sub="% of the fund, by the month a trade closed" testid="fund-months">
        <MonthBars months={v.months} target={h.target_pct} stop={h.stop_pct ?? 10} names={NAMES} />
        <More label="Month by month" className="mt-2" testid="fund-months-more">
          <div className="max-h-[360px] overflow-auto scroll-thin">
            <table className="desk-table">
              <thead><tr><th>Month</th><th className="num">Fund</th><th className="num">Net</th><th className="num">Elite 4 Edge</th><th className="num">Option Picks</th><th className="num">Flush Short</th><th className="num">Taken</th><th className="num">Not taken</th><th>Stops</th></tr></thead>
              <tbody>{[...v.months].reverse().map((m) => (
                <tr key={m.month}><td className="font-mono">{m.month}</td><td className={`num font-semibold ${tone(m.pct)}`}>{pc(m.pct)}</td><td className={`num ${tone(m.net)}`}>{signedUsd(m.net, 0)}</td>
                  {["elite", "optpicks", "flush"].map((k) => <td key={k} className={`num ${tone(m.by[k])}`}>{signedUsd(m.by[k], 0)}</td>)}
                  <td className="num">{m.taken}</td><td className="num">{m.skipped || ""}</td><td className="whitespace-normal text-[11.5px] text-slate-400">{m.stops.join(" ")}</td></tr>))}
              </tbody>
            </table>
          </div>
        </More>
      </Card>
      <div className="grid gap-3 lg:grid-cols-2">
        <Card title="Fund P&L" sub="Closed trades, after fees" testid="fund-curve">
          <LineChart series={[{ name: "Net P&L $", color: VIZ.s1, points: v.curve }]} xFmt={(t) => ist(t)} yFmt={(x) => n(x, 0)} unit=" $" height={170} />
        </Card>
        <Card title="Bigger size is not a bigger return" sub="Same capital, every position multiplied, same stops" testid="fund-scale" pad={false}>
          <div className="overflow-x-auto scroll-thin">
            <table className="desk-table">
              <thead><tr><th>Size</th><th className="num">Avg month</th><th className="num">Hit {h.target_pct}%</th><th className="num">Losing</th><th className="num">Worst</th><th className="num">Deepest fall</th><th className="num">Stops</th></tr></thead>
              <tbody>{h.scale.map((x) => (
                <tr key={x.size} className={x.size === 1 ? "bg-amber-500/[0.06]" : ""}><td className="whitespace-nowrap font-mono">{n(x.size, 2)}×{x.size === 1 ? " plan" : ""}</td><td className={`num ${tone(x.avg_pct)}`}>{pc(x.avg_pct)}</td>
                  <td className="num whitespace-nowrap">{x.hit} of {x.months}</td><td className="num">{x.negative}</td><td className="num text-red-300">{pc(x.worst_pct)}</td><td className="num text-red-300">{pc(x.max_dd_pct)}</td><td className="num">{x.stops}</td></tr>))}
              </tbody>
            </table>
          </div>
        </Card>
      </div>
      <Card title="By strategy in this view" pad={false} testid="fund-history-by">
        <table className="desk-table">
          <thead><tr><th>Strategy</th><th className="num">Net for the fund</th><th className="num">Its deepest fall</th><th className="num">Trades taken</th><th className="num">Not taken</th></tr></thead>
          <tbody>{["elite", "optpicks", "flush"].map((k) => (
            <tr key={k}><td>{NAMES[k]}</td><td className={`num ${tone(v.by[k].net)}`}>{signedUsd(v.by[k].net, 0)}</td><td className="num text-red-300">{usd0(v.by[k].max_dd)}</td><td className="num">{v.by[k].taken}</td><td className="num">{v.by[k].skipped}</td></tr>))}
          </tbody>
        </table>
      </Card>
      <Note>This is a backtest of your plan, not live trading. Open trades run to their own exit after a stop, so a month can end below the month stop. {view === "stress" ? "Worse option fills = every option sold 5% cheaper and bought back 5% dearer, with double fees. " : ""}{h.flush_note}</Note>
    </div>
  );
}

function Journal({ d }) {
  const [rows, pager] = usePaged(d.ledger, 100);
  return (
    <div className="grid gap-3 xl:grid-cols-[1.4fr_1fr]">
      <Card title="Fund journal" sub="Every strategy trade since the fund started, and what the fund decided" pad={false} testid="fund-ledger">
        <div className="overflow-x-auto scroll-thin">
          <table className="desk-table">
            <thead><tr><th>Entry (IST)</th><th>Strategy</th><th>Trade</th><th>Decision</th><th className="num">Size</th><th className="num">Margin</th><th>Exit (IST)</th><th className="num">Net for the fund</th></tr></thead>
            <tbody>{rows.map((r) => (
              <tr key={r.id} title={r.reason || ""}><td className="font-mono">{r.entry_ist}</td><td>{r.name}</td><td>{r.label}</td>
                <td><Chip status={r.decision} />{r.reason && <div className="max-w-[260px] whitespace-normal text-[11px] text-slate-500">{r.reason}</div>}</td>
                <td className="num">{n(r.k, 2)}×</td><td className="num">{r.decision === "TAKEN" ? usd0(r.margin) : "—"}</td><td className="font-mono">{r.exit_ist || (r.status === "OPEN" ? "open" : "—")}</td>
                <td className={`num font-semibold ${tone(r.net)}`}>{r.net == null ? "—" : `${signedUsd(r.net)}${r.status === "OPEN" ? " open" : ""}`}</td></tr>))}
            </tbody>
          </table>
          {pager}
          {!d.ledger.length && <div className="p-4 text-center text-xs text-slate-500">No strategy has traded since the fund started on {ist(d.fund.start)}.</div>}
        </div>
      </Card>
      <Card title="Decision log" sub="Starts, trades taken or not, stops, resumes and setting changes" testid="fund-events">
        <div className="max-h-[460px] overflow-auto scroll-thin text-[12px]">
          {(d.events || []).map((e, i) => <div key={i} className={`border-b border-line/60 py-1 ${e.level === "error" ? "text-red-300" : e.level === "warn" ? "text-amber-300" : "text-slate-300"}`}><span className="font-mono text-slate-500">{ist(e.at)}</span> {e.text}</div>)}
        </div>
      </Card>
    </div>
  );
}

function Settings({ d, reload, act, busy }) {
  const s = d.settings;
  const [v, setV] = useState(null);
  const [saving, setSaving] = useState(false);
  const [word, setWord] = useState("");
  useEffect(() => { setV({ capital: s.capital, target_pct: s.target_pct, month_loss_pct: s.month_loss_pct, fund_dd_pct: s.fund_dd_pct, max_use_pct: s.max_use_pct, lock_at_target: s.lock_at_target, alloc: { ...s.alloc }, strat_dd_pct: { ...s.strat_dd_pct } }); }, [JSON.stringify(s)]); // eslint-disable-line react-hooks/exhaustive-deps
  if (!v) return null;
  const num = (x) => (x === "" || x == null ? null : Number(x));
  const sum = ["elite", "optpicks", "flush"].reduce((a, k) => a + (Number(v.alloc[k]) || 0), 0);
  const save = async () => {
    setSaving(true);
    try {
      await api.put("/fund/settings", { capital: num(v.capital), target_pct: num(v.target_pct), month_loss_pct: num(v.month_loss_pct), fund_dd_pct: num(v.fund_dd_pct), max_use_pct: num(v.max_use_pct),
        lock_at_target: !!v.lock_at_target, alloc: Object.fromEntries(Object.entries(v.alloc).map(([k, x]) => [k, Number(x) || 0])), strat_dd_pct: Object.fromEntries(Object.entries(v.strat_dd_pct).map(([k, x]) => [k, Number(x) || 0])) });
      toast.success("Plan saved. It applies to new trades."); reload();
    } catch (e) { toast.error(errText(e)); }
    setSaving(false);
  };
  const F = ({ k, label, hint }) => <Field label={label} hint={hint}><Input type="number" value={v[k] ?? ""} onChange={(e) => setV({ ...v, [k]: e.target.value })} data-testid={`fund-set-${k}`} /></Field>;
  return (
    <div className="space-y-3" data-testid="fund-settings">
      <Card title="Plan" sub="Paper fund. A change applies to new trades; trades already decided keep their size.">
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-5">
          {F({ k: "capital", label: "Fund capital ($)" })}
          {F({ k: "target_pct", label: "Monthly target (%)" })}
          {F({ k: "month_loss_pct", label: "Month stop (% loss)", hint: "no new trades until next month" })}
          {F({ k: "fund_dd_pct", label: "Fund stop (% from peak)", hint: "needs Resume all" })}
          {F({ k: "max_use_pct", label: "Most money in use (%)" })}
        </div>
        <div className="mt-3 overflow-x-auto scroll-thin">
          <table className="desk-table">
            <thead><tr><th>Strategy</th><th className="num">Share of the fund (%)</th><th className="num">Capital</th><th className="num">Size</th><th className="num">Pause after falling (% of its capital)</th><th className="num">= in $</th></tr></thead>
            <tbody>{d.strategies.map((x) => { const capS = (Number(v.capital) || 0) * (Number(v.alloc[x.key]) || 0) / 100; return (
              <tr key={x.key}><td>{x.name}</td>
                <td className="num"><Input type="number" className="!w-24 text-right" value={v.alloc[x.key] ?? ""} onChange={(e) => setV({ ...v, alloc: { ...v.alloc, [x.key]: e.target.value } })} data-testid={`fund-alloc-${x.key}`} /></td>
                <td className="num">{usd0(capS)}</td><td className="num">{x.unit_capital ? n(capS / x.unit_capital, 2) : "—"}×</td>
                <td className="num"><Input type="number" className="!w-24 text-right" value={v.strat_dd_pct[x.key] ?? ""} onChange={(e) => setV({ ...v, strat_dd_pct: { ...v.strat_dd_pct, [x.key]: e.target.value } })} data-testid={`fund-dd-${x.key}`} /></td>
                <td className="num">{usd0(capS * (Number(v.strat_dd_pct[x.key]) || 0) / 100)}</td></tr>); })}
              <tr className="border-t-2 border-line2 font-semibold"><td>Total</td><td className={`num ${sum > 100 ? "text-red-300" : ""}`}>{n(sum, 0)}%</td><td className="num">{usd0((Number(v.capital) || 0) * sum / 100)}</td><td /><td className="num font-normal text-slate-500">cash reserve {n(Math.max(0, 100 - sum), 0)}%</td><td /></tr>
            </tbody>
          </table>
        </div>
        <label className="mt-3 flex items-center gap-2 text-[12.5px] text-slate-300"><input type="checkbox" checked={!!v.lock_at_target} onChange={(e) => setV({ ...v, lock_at_target: e.target.checked })} data-testid="fund-set-lock" /> When the monthly target is reached, take no new trades until next month</label>
        <div className="mt-3 flex flex-wrap items-center gap-2"><Btn disabled={saving || sum > 100} onClick={save} data-testid="fund-save">{saving ? "Saving…" : "Save plan"}</Btn>{sum > 100 && <span className="text-[12px] text-red-300">The shares add up to more than 100%.</span>}</div>
        <Note tone="plain" className="mt-2">{d.why_defaults}</Note>
      </Card>
      <Card title="Controls">
        <div className="flex flex-wrap items-center gap-2">
          {d.fund.stop_all || d.fund.month.stopped
            ? <Btn tone="teal" disabled={busy} onClick={() => act({ action: "resume_all" }, "Fund resumed")} data-testid="fund-resume-all">Resume all</Btn>
            : <Btn tone="red" disabled={busy} onClick={() => act({ action: "stop_all" }, "Fund stopped")} data-testid="fund-stop-all">Stop all</Btn>}
          <span className="mx-2 h-5 w-px bg-line" />
          <Input className="!w-36" placeholder="type RESTART" value={word} onChange={(e) => setWord(e.target.value)} data-testid="fund-restart-word" />
          <Btn tone="ghost" disabled={busy || word.trim().toUpperCase() !== "RESTART"} onClick={() => { act({ action: "restart", confirm: word }, "New fund book started"); setWord(""); }} data-testid="fund-restart">Start a new fund book</Btn>
        </div>
        <Note tone="plain" className="mt-2">Stop all and Pause only stop the fund from taking new trades; the strategy pages keep their own paper journals. A new fund book sets P&L, peaks and stops back to zero from now and keeps the plan.</Note>
      </Card>
    </div>
  );
}

export default function FundManager() {
  const { data: d, reload } = useFetch("/fund", 30000);
  const [tab, setTab] = useState("now");
  const [busy, setBusy] = useState(false);
  if (!d) return <Empty>Loading the fund…</Empty>;
  if (d.loading) return <div className="space-y-3"><PageHead title="Fund Manager" /><Note>The fund is being worked out. This takes about four minutes after a restart, once the three strategies have finished their own calculations.{d.error ? ` Last error: ${d.error}` : ""}</Note></div>;
  const act = async (body, ok) => {
    setBusy(true);
    try { await api.post("/fund/control", body); toast.success(ok); reload(); } catch (e) { toast.error(errText(e)); }
    setBusy(false);
  };
  const f = d.fund, sm = d.integrity?.summary;
  const tabs = [["now", "Now"], ["checks", `Signal check${sm ? ` · ${sm.ok}/${sm.total}` : ""}`], ["history", "History"], ["journal", "Journal"]];
  if (d.is_admin) tabs.push(["settings", "Plan and controls"]);
  return (
    <div className="space-y-3" data-testid="fund-page">
      <PageHead title="Fund Manager"
        sub={`One paper fund over Elite 4 Edge, Option Picks and Flush Short: it splits the capital, sizes each strategy, limits the money in use, stops a strategy or the whole fund at its loss limits and tracks the monthly target. It follows the strategies' paper trades and never sends an order. Started ${ist(f.start)} · updated ${ist(d.computed_at)}.`}
        right={<span className="flex items-center gap-2"><Chip status="PAPER" testid="fund-mode" /><Chip status={f.stop_all || f.month.stopped ? "STOPPED" : "ACTIVE"} testid="fund-status" /></span>} />
      {d.error && <Note tone="red">Last update failed: {d.error}</Note>}
      <KpiStrip testid="fund-kpi" items={[
        { label: "Fund value", value: usd0(f.value), sub: `capital ${usd0(f.capital)} · open ${signedUsd(f.open, 0)}` },
        { label: `This month (${f.month.key})`, value: signedUsd(f.month.net, 0), cls: tone(f.month.net), sub: `${pc(f.month.pct, 2)} of the fund` },
        { label: `To the ${f.month.target_pct}% target`, value: f.month.target_hit ? "reached" : usd0(f.month.gap), cls: f.month.target_hit ? "text-teal-300" : "text-slate-50", sub: f.month.target_hit ? `target ${usd0(f.month.target)}` : `${usd0(f.month.per_day)} a day for ${f.month.days_left} days` },
        { label: "Money in use", value: `${n(f.in_use_pct, 0)}%`, sub: `${usd0(f.in_use)} · cap ${f.max_use_pct}%` },
        { label: "From the peak", value: usd0(f.dd), cls: f.dd < 0 ? "text-red-300" : "text-slate-50", sub: `fund stop at ${usd0(-f.dd_limit)}` },
        { label: "Signal check", value: sm ? `${sm.ok} / ${sm.total}` : "—", cls: sm && (sm.fail || sm.warn) ? "text-amber-300" : "text-teal-300", sub: sm ? (sm.fail || sm.warn ? `${sm.fail + sm.warn} to look at` : sm.wait ? `${sm.wait} waiting for data` : "all pass") : "running" },
      ]} />
      <Tabs testid="fund-tab" value={tab} onChange={setTab} items={tabs} />
      {tab === "now" && <Overview d={d} act={act} busy={busy} />}
      {tab === "checks" && <Checks integ={d.integrity} />}
      {tab === "history" && <History h={d.history} />}
      {tab === "journal" && <Journal d={d} />}
      {tab === "settings" && d.is_admin && <Settings d={d} reload={reload} act={act} busy={busy} />}
    </div>
  );
}
