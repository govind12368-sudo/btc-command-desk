import React, { useState } from "react";
import { useFetch } from "@/hooks/useFetch";
import { signedUsd } from "@/lib/fmt";

// Viewing filter for the journal tables: day type, what the Day-type switch did, and the support recorded when the
// signal fired. It only hides rows on the screen; no total, book or trade is changed by it.
export const DAYS = [["", "All"], ["RANGE", "Range"], ["TREND_UP", "Trend up"], ["TREND_DOWN", "Trend down"], ["MIXED", "Unclear"]];
export const ACTIONS = [["", "All"], ["PLAN_A", "Plan A"], ["FOLLOW", "Follow"], ["STAND_ASIDE", "Stood aside"]];
export const LEVELS = [["", "All"], ["STRONG", "Strong"], ["MEDIUM", "Medium"], ["WEAK", "Weak"]];
const LEVEL_NAME = { STRONG: "Strong", MEDIUM: "Medium", WEAK: "Weak" };
const LEVEL_INK = { STRONG: "text-teal-300", MEDIUM: "text-amber-300", WEAK: "text-slate-300" };
const EMPTY = { day: "", action: "", level: "" };
// The Auto-Trade journal stores the day type as its label.
export const LABEL_TYPE = { Range: "RANGE", "Trend (up)": "TREND_UP", "Trend (down)": "TREND_DOWN", Unclear: "MIXED" };

export function useDayFilter(key) {
  const store = `vantage-dayfilter-${key}`;
  const [f, setState] = useState(() => {
    try { return { ...EMPTY, ...(JSON.parse(window.localStorage.getItem(store)) || {}) }; } catch (e) { return EMPTY; }
  });
  const setF = (next) => {
    setState(next);
    try { window.localStorage.setItem(store, JSON.stringify(next)); } catch (e) { /* remembering the choice is optional */ }
  };
  return [f, setF];
}

export function useSupport() {
  const { data } = useFetch("/switch-support", 60000);
  return data?.items || {};
}

export const matches = (f, type, playbook, level) => (!f.day || type === f.day) && (!f.action || playbook === f.action) && (!f.level || level === f.level);
export const isActive = (f) => Boolean(f.day || f.action || f.level);

function Group({ label, opts, value, onChange, testid }) {
  return (
    <div className="flex flex-wrap items-center gap-1.5" role="group" aria-label={label}>
      <span className="w-[88px] shrink-0 text-[11.5px] text-slate-500">{label}</span>
      {opts.map(([k, l]) => (
        <button key={k || "all"} type="button" aria-pressed={value === k} data-testid={`${testid}-${k || "all"}`} onClick={() => onChange(k)}
          className={`rounded border px-2.5 py-1 text-[11.5px] font-semibold transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-amber-400 ${value === k ? "border-amber-500/60 bg-amber-500/10 text-amber-300" : "border-[#1e2a4a] text-slate-400 hover:border-slate-500 hover:text-slate-200"}`}>{l}</button>
      ))}
    </div>
  );
}

export function DayFilterBar({ f, setF, stoodAside = true, testid = "dayfilter" }) {
  return (
    <div className="mb-3 space-y-1.5 rounded-lg border border-[#1e2a4a] bg-[#0f172a]/60 px-3 py-2.5" data-testid={testid}>
      <Group label="Day type" opts={DAYS} value={f.day} onChange={(v) => setF({ ...f, day: v })} testid={`${testid}-day`} />
      <Group label="Switch did" opts={stoodAside ? ACTIONS : ACTIONS.filter(([k]) => k !== "STAND_ASIDE")} value={f.action} onChange={(v) => setF({ ...f, action: v })} testid={`${testid}-action`} />
      <Group label="Support" opts={LEVELS} value={f.level} onChange={(v) => setF({ ...f, level: v })} testid={`${testid}-level`} />
    </div>
  );
}

// One line under the table's filter: how many rows match and how those trades did. Shown only while a filter is on.
export function FilterSummary({ f, setF, shown, total, what, trades, wins, net, basis }) {
  if (!isActive(f)) return null;
  const rate = trades ? `${Math.round((wins / trades) * 100)}%` : "—";
  return (
    <p className="mb-3 text-[12.5px] text-slate-300" data-testid="dayfilter-summary">
      Showing <b className="text-slate-50">{shown}</b> of {total} {what}. {trades != null && <>Closed trades in this view: <b className="font-mono text-slate-50">{trades}</b> · win rate <b className="font-mono text-slate-50">{rate}</b> · net <b className={`font-mono ${(net || 0) >= 0 ? "text-teal-300" : "text-red-300"}`}>{signedUsd(net)}</b>{basis ? ` (${basis})` : ""}. </>}
      {trades != null && trades < 20 && <span className="text-slate-500">Only {trades} trade{trades === 1 ? "" : "s"}: too few to judge. </span>}
      <button type="button" onClick={() => setF(EMPTY)} className="font-semibold text-amber-400 underline-offset-2 hover:underline" data-testid="dayfilter-clear">Clear filter</button>
    </p>
  );
}

// Support recorded when the signal fired, shown under the day type. Click to see the five checks.
export function SupportLine({ sup, playbook }) {
  const [open, setOpen] = useState(false);
  if (playbook !== "PLAN_A" && playbook !== "FOLLOW") return null;
  if (!sup?.level) return <span className="block text-[10.5px] text-slate-600">Support: not recorded</span>;
  return (
    <span className="block text-[10.5px]">
      <button type="button" aria-expanded={open} onClick={() => setOpen(!open)} className={`font-semibold underline decoration-dotted underline-offset-2 focus:outline-none focus-visible:ring-2 focus-visible:ring-amber-400 ${LEVEL_INK[sup.level]}`}>Support: {LEVEL_NAME[sup.level]} {sup.passed} of {sup.of}</button>
      {open && <span className="mt-1 block min-w-[230px] max-w-[300px] whitespace-normal rounded border border-[#1e2a4a] bg-[#0b132b] p-2 text-left leading-snug">
        {(sup.checks || []).map((c) => <span key={c.key} className={`block ${c.ok ? "text-slate-200" : "text-slate-500"}`}>{c.ok == null ? "—" : c.ok ? "✓" : "✗"} <b>{c.name}.</b> {c.text}</span>)}
      </span>}
    </span>
  );
}
