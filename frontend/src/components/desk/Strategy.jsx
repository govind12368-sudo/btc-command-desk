import React, { useState } from "react";
import { Link } from "react-router-dom";
import { Chip, KV, Expand } from "./ui";
import { n, usd, ist, pct } from "@/lib/fmt";

export function ChecksList({ ev, testid }) {
  const [open, setOpen] = useState({});
  if (!ev) return null;
  return (
    <div data-testid={testid} className="divide-y divide-[#1e2a4a]/70">
      {(ev.checks || []).map((c) => {
        const expanded = open[c.id] ?? (c.status === "WAITING SETUP" || c.status === "UNAVAILABLE");
        return (
        <div key={c.id} className="py-1.5">
          <button data-testid={`${testid}-${c.id}`} aria-expanded={expanded} onClick={() => setOpen({ ...open, [c.id]: !expanded })} className="flex w-full items-center gap-2 text-left">
            <span className="w-9 font-mono text-[11px] text-amber-400/80">{c.id}</span>
            <span className="flex-1 text-[13px] text-slate-200">{c.name}</span>
            <Chip status={c.status} />
          </button>
          {expanded && <p className="ml-11 mt-1 font-mono text-[11px] leading-relaxed text-slate-400">{c.detail}</p>}
        </div>
        );
      })}
      {ev.excluded_checks?.length > 0 && <p className="py-2 text-[11px] text-slate-500">{ev.excluded_checks.map((c) => `${c.id} ${c.name}`).join(" · ")} is advisory and excluded from this checklist. Review and journal records remain available.</p>}
    </div>
  );
}

export function CompareTable({ scalp, swing }) {
  if (!scalp || !swing) return null;
  const scalpById = Object.fromEntries((scalp.checks || []).map((c) => [c.id, c]));
  const swingById = Object.fromEntries((swing.checks || []).map((c) => [c.id, c]));
  const ids = [...new Set([...Object.keys(scalpById), ...Object.keys(swingById)])].sort((a, b) => a.localeCompare(b, undefined, { numeric: true }));
  return (
    <div className="overflow-x-auto scroll-thin">
      <table className="desk-table" data-testid="checks-compare-table">
        <thead><tr><th>ID</th><th>Check</th><th>Scalping</th><th className="hidden md:table-cell">Scalping detail</th><th>Swing</th><th className="hidden md:table-cell">Swing detail</th></tr></thead>
        <tbody>
          {ids.map((id) => (
            <tr key={id}>
              <td className="font-mono text-amber-400/80">{id}</td>
              <td className="text-slate-200 whitespace-nowrap">{scalpById[id]?.name || swingById[id]?.name || id}</td>
              <td><Chip status={scalpById[id]?.status || "UNAVAILABLE"} testid={`cmp-scalp-${id}`} /></td>
              <td className="hidden md:table-cell font-mono text-[11px] text-slate-400 max-w-[340px]">{scalpById[id]?.detail || "Check not supplied"}</td>
              <td><Chip status={swingById[id]?.status || "UNAVAILABLE"} testid={`cmp-swing-${id}`} /></td>
              <td className="hidden md:table-cell font-mono text-[11px] text-slate-400 max-w-[340px]">{swingById[id]?.detail || "Check not supplied"}</td>
            </tr>
          ))}
          <tr><td /><td className="font-semibold">Passed</td><td className="font-mono" data-testid="cmp-scalp-total">{scalp.passed}/{scalp.total_checks ?? scalp.checks?.length ?? 0}</td><td className="hidden md:table-cell" /><td className="font-mono" data-testid="cmp-swing-total">{swing.passed}/{swing.total_checks ?? swing.checks?.length ?? 0}</td><td className="hidden md:table-cell" /></tr>
        </tbody>
      </table>
      <p className="mt-2 text-[11px] text-slate-500">F31 review/journal is advisory and excluded from both totals; existing records are retained.</p>
    </div>
  );
}

export function Levels({ lv, testid }) {
  if (!lv) return <p className="text-xs text-slate-500" data-testid={testid}>No levels — a qualified direction with valid data and contract spec is required.</p>;
  return (
    <div data-testid={testid}>
      <KV k="Direction" v={<Chip status={lv.direction} />} />
      <KV k="Entry (USD)" v={<span>{n(lv.entry, 1)}{lv.entry_type && <span className={`ml-2 rounded px-1.5 py-0.5 text-[10px] font-semibold ${lv.entry_type === "POST-ONLY LIMIT" ? "bg-teal-500/10 text-teal-300" : "bg-slate-500/10 text-slate-300"}`}>{lv.entry_type}</span>}</span>} testid={`${testid}-entry`} />
      {lv.valid_until && <KV k="Limit valid until" v={`${ist(lv.valid_until * 1000)} · cancel if not filled`} testid={`${testid}-valid`} />}
      {lv.entry_type && <KV k="Exit orders" v={`Target ${lv.target_type === "LIMIT" ? "limit (maker)" : "market"} · stop ${lv.stop_type === "STOP-MARKET" ? "stop-market" : lv.stop_type}`} testid={`${testid}-exits`} />}
      <KV k="Stop (USD)" v={<span className="text-red-300">{n(lv.stop, 1)}</span>} testid={`${testid}-stop`} />
      <KV k="Target (USD)" v={<span className="text-teal-300">{n(lv.target, 1)}</span>} testid={`${testid}-target`} />
      <KV k="Quantity" v={`${n(lv.qty_btc, 3)} BTC · ${lv.contracts} contracts · ${usd(lv.notional_usd, 0)} notional`} testid={`${testid}-qty`} />
      <KV k="Cost-adjusted reward/risk" v={<span className={lv.net_r > 1 ? "text-teal-300" : "text-red-300"}>{n(lv.net_r, 3)} R</span>} testid={`${testid}-netr`} />
      <Expand testid={`${testid}-calc`}>
        <div className="space-y-1 font-mono text-[11px]">
          {lv.legs ? (<>
            <div>costs per side (fee incl. GST, + slippage on taker legs): entry {n(lv.legs.entry, 5)} · target {n(lv.legs.target, 5)} · stop {n(lv.legs.stop, 5)}</div>
            <div>risk/BTC = |entry−stop| + entry·c_entry + stop·c_stop + entry·funding = {n(lv.risk_per_btc, 2)}</div>
            <div>reward/BTC = |target−entry| − entry·c_entry − target·c_target − entry·funding = {n(lv.reward_per_btc, 2)}</div>
          </>) : (<>
            <div>c = fee + slippage = {n(lv.c, 5)} per side</div>
            <div>risk/BTC = |entry−stop| + (entry+stop)·c + entry·funding = {n(lv.risk_per_btc, 2)}</div>
            <div>reward/BTC = |target−entry| − (entry+target)·c − entry·funding = {n(lv.reward_per_btc, 2)}</div>
          </>)}
          <div>size limits (BTC): max notional {n(lv.size_limits?.by_max_notional, 4)}, risk {n(lv.size_limits?.by_risk, 4)}, leverage {n(lv.size_limits?.by_leverage, 4)} → floor to 0.001</div>
          <div>risk {usd(lv.risk_usd)} · reward {usd(lv.reward_usd)} · breakeven hit rate {pct(lv.breakeven_fraction, 1)}</div>
          <div>ATR {n(lv.atr, 1)} · completed close {n(lv.close, 1)}{lv.entry_live ? ` · live entry ${n(lv.entry_live, 1)}` : ""}</div>
        </div>
      </Expand>
    </div>
  );
}

export function StrategyCard({ ev, title, to }) {
  if (!ev) return null;
  const tfs = Object.keys(ev.frames || {});
  return (
    <div className="desk-card p-4 sm:p-5 fade-up" data-testid={`${ev.strategy}-signal-card`}>
      <div className="mb-3 flex items-start justify-between gap-2">
        <div>
          <h3 className="text-[15px] font-semibold">{title}</h3>
          <p className="font-mono text-[11px] text-slate-500">{tfs.join(" + ")} completed candles · higher trend {ev.higher_trend || "—"}</p>
        </div>
        <div className="text-right">
          <Chip status={ev.status} testid={`${ev.strategy}-status`} className="text-xs px-3 py-1" />
          <div className="mt-1 font-mono text-[11px] text-slate-400" data-testid={`${ev.strategy}-passed`}>{ev.passed}/{ev.total_checks ?? ev.checks?.length ?? 0} checks</div>
        </div>
      </div>
      <Levels lv={ev.levels} testid={`${ev.strategy}-levels`} />
      {ev.failing?.length > 0 && <p className="mt-2 font-mono text-[11px] text-slate-500">Not passing: {ev.failing.join(", ")}</p>}
      {to && <Link to={to} data-testid={`${ev.strategy}-open-page`} className="mt-3 inline-block text-xs text-amber-400 hover:text-amber-300">Open full {title.toLowerCase()} →</Link>}
    </div>
  );
}

export function FrameTable({ frames }) {
  return (
    <div className="overflow-x-auto scroll-thin">
      <table className="desk-table">
        <thead><tr><th>Frame</th><th>Bars</th><th>Close</th><th>EMA20</th><th>EMA50</th><th>Prev EMA20</th><th>ATR14</th><th>VWAP20</th><th>Prior-20 H / L</th><th>Vol ratio</th><th>Closed</th></tr></thead>
        <tbody>
          {Object.entries(frames || {}).map(([tf, m]) => (
            <tr key={tf}>
              <td className="font-mono text-amber-300">{tf}</td><td className="font-mono">{m.bars}</td>
              {m.valid || m.close ? (<>
                <td className="font-mono">{n(m.close, 1)}</td><td className="font-mono">{n(m.ema20, 1)}</td><td className="font-mono">{n(m.ema50, 1)}</td><td className="font-mono">{n(m.ema20_prev, 1)}</td>
                <td className="font-mono">{n(m.atr14, 1)}</td><td className="font-mono">{n(m.vwap20, 1)}</td><td className="font-mono">{n(m.prior20_high, 1)} / {n(m.prior20_low, 1)}</td>
                <td className="font-mono">{n(m.volume_ratio, 2)}</td><td className="font-mono text-[11px]">{ist(m.bar_close)}</td></>) : <td colSpan={9} className="text-red-300 text-xs">{(m.issues || []).join("; ") || "unavailable"}</td>}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
