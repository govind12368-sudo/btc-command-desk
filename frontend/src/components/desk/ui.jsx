import React, { useEffect, useLayoutEffect, useRef, useState } from "react";
import { ChevronDown, Info } from "lucide-react";

/* Vantage terminal UI kit.
   Numbers first, words on demand: long sub-titles, page descriptions and info notes fold behind an ⓘ / "More" toggle.
   Green/red only for P&L and direction, gold for brand and selection, graphite for everything else. */

const STYLES = {
  teal: "bg-teal-500/10 text-teal-300 border-teal-500/35",
  amber: "bg-amber-500/10 text-amber-300 border-amber-500/35",
  indigo: "bg-indigo-500/10 text-indigo-300 border-indigo-400/35",
  red: "bg-red-500/10 text-red-300 border-red-500/40",
  slate: "bg-slate-700/25 text-slate-300 border-slate-600/50",
  gray: "bg-slate-800/40 text-slate-500 border-slate-700/60",
};
const MAP = {
  PASS: "teal", OK: "teal", BUY: "teal", LONG: "teal", "TARGET FIRST": "teal", RECOVERED: "teal", CONNECTED: "teal", AGREE: "teal", ACTIVE: "teal", REVIEWED: "teal", UP: "teal",
  WAIT: "amber", "PLAN ONLY": "amber", "READY TO CAPTURE": "amber", PENDING: "amber", PAUSED: "amber", CHECKING: "amber", "COOLING DOWN": "amber", NEUTRAL: "slate", MIXED: "slate",
  REVIEW: "indigo", MONITOR: "indigo", "SAME CANDLE REVIEW": "indigo", "CAPTURE CANDLE REVIEW": "indigo", UNREVIEWED: "indigo", CONSUMED: "indigo",
  BLOCKED: "red", FAIL: "red", SELL: "red", SHORT: "red", "STOP FIRST": "red", ERROR: "red", CONFLICT: "red", INVALIDATED: "red", DOWN: "red",
  "STILL NEEDS ATTENTION": "red", "SOURCE ERROR": "red", "INVALID SNAPSHOT": "red", CRITICAL: "red", HIGH: "amber", MEDIUM: "indigo", LOW: "slate",
  STALE: "slate", EXPIRED: "slate", CANCELLED: "slate", IGNORED: "slate", RECONNECTING: "amber", IDLE: "slate", LIVE: "red", PAPER: "slate", TESTNET: "amber",
  TRADE: "teal", TAKEN: "teal", TARGET: "teal", "IN TRADE": "indigo", "STAND ASIDE": "slate", STOPPED: "red", CHECK: "amber", WARN: "amber", SKIPPED: "slate", INFO: "slate", OFF: "gray",
  UNAVAILABLE: "gray", "HISTORY GAP": "gray", ACCESS_NEEDED: "gray", "PROVIDER ACCESS/INPUT NEEDED": "gray", RATE_LIMITED: "amber",
};

export const statusLabel = (s) => {
  const u = String(s || "UNAVAILABLE").toUpperCase();
  return { OK: "PASS", ERROR: "ERROR", ACCESS_NEEDED: "ACCESS NEEDED", RATE_LIMITED: "RATE LIMITED" }[u] || u;
};

export function Chip({ status, label, testid, className = "" }) {
  const u = String(status || "UNAVAILABLE").toUpperCase();
  const st = STYLES[MAP[u] || (u.includes("BULL") ? "teal" : u.includes("BEAR") ? "red" : "slate")];
  return (
    <span data-testid={testid} className={`inline-flex items-center gap-1 rounded-sm border px-1.5 py-px font-mono text-[10px] font-semibold tracking-wide whitespace-nowrap ${st} ${className}`}>
      {label || statusLabel(status)}
    </span>
  );
}

const textLen = (x) => (typeof x === "string" ? x.length : Array.isArray(x) ? x.reduce((a, y) => a + textLen(y), 0) : x && x.props ? textLen(x.props.children) : x ? 20 : 0);

/** ⓘ button that shows/hides a block of explanation. */
export function InfoToggle({ open, onClick, label = "More info", testid }) {
  return (
    <button type="button" onClick={onClick} data-testid={testid} aria-expanded={open} title={open ? "Hide details" : label}
      className={`inline-flex h-5 w-5 shrink-0 items-center justify-center rounded-sm border transition-colors ${open ? "border-amber-500/60 text-amber-300" : "border-line text-slate-500 hover:border-line2 hover:text-slate-200"}`}>
      <Info size={11} />
    </button>
  );
}

/** Panel with a terminal title bar. A short `sub` shows in the bar; a long one folds behind ⓘ. */
export function Card({ title, right, children, className = "", testid, sub, pad = true }) {
  const [open, setOpen] = useState(false);
  const long = sub && textLen(sub) > 70;
  return (
    <section data-testid={testid} className={`desk-card ${className}`}>
      {(title || right) && (
        <header className="desk-card-head">
          <div className="flex min-w-0 items-center gap-2">
            {title && <h3 className="truncate text-[13px] font-semibold tracking-tight text-slate-100">{title}</h3>}
            {sub && !long && <span className="hidden truncate font-mono text-[10.5px] text-slate-500 sm:inline">{sub}</span>}
            {long && <InfoToggle open={open} onClick={() => setOpen(!open)} />}
          </div>
          {right && <div className="flex shrink-0 flex-wrap items-center gap-1.5">{right}</div>}
        </header>
      )}
      {long && open && <div className="border-b border-line bg-panel2/70 px-3 py-2 text-[12px] leading-relaxed text-slate-400">{sub}</div>}
      <div className={pad ? "p-3" : ""}>{children}</div>
    </section>
  );
}

export function KV({ k, v, mono = true, testid, className = "" }) {
  return (
    <div className={`flex items-baseline justify-between gap-3 border-b border-line/50 py-[5px] last:border-0 ${className}`}>
      <span className="text-[12px] text-slate-400">{k}</span>
      <span data-testid={testid} className={`${mono ? "font-mono" : ""} text-right text-[12.5px] text-slate-100`}>{v}</span>
    </div>
  );
}

/** KPI tile: label, big mono value, optional short sub-line (long subs move to a tooltip). */
export function Stat({ label, value, sub, testid, valueClass = "" }) {
  const long = sub && textLen(sub) > 48;
  return (
    <div className="desk-card px-3 py-2" title={long && typeof sub === "string" ? sub : undefined}>
      <div className="truncate font-mono text-[10.5px] text-slate-400">{label}</div>
      <div data-testid={testid} className={`mt-0.5 truncate font-mono text-[19px] font-semibold leading-tight text-slate-50 ${valueClass}`}>{value}</div>
      {sub && <div className={`mt-0.5 text-[11px] text-slate-500 ${long ? "truncate" : ""}`}>{sub}</div>}
    </div>
  );
}

/** Horizontal KPI strip (terminal header row): [{label, value, cls, sub}] */
export function KpiStrip({ items, testid, className = "" }) {
  return (
    <div data-testid={testid} className={`desk-card grid divide-line overflow-hidden ${className}`} style={{ gridTemplateColumns: `repeat(auto-fit, minmax(128px, 1fr))` }}>
      {items.filter(Boolean).map((it, i) => (
        <div key={i} className="border-b border-r border-line/70 px-3 py-2" title={it.title}>
          <div className="truncate font-mono text-[10.5px] text-slate-400">{it.label}</div>
          <div className={`truncate font-mono text-[17px] font-semibold leading-snug ${it.cls || "text-slate-50"}`}>{it.value}</div>
          {it.sub && <div className="truncate text-[11px] text-slate-500">{it.sub}</div>}
        </div>
      ))}
    </div>
  );
}

/** Disclosure for explanations: closed by default. */
export function More({ label = "More info", children, testid, className = "" }) {
  const [open, setOpen] = useState(false);
  return (
    <div className={className}>
      <button type="button" data-testid={testid} onClick={() => setOpen(!open)} aria-expanded={open}
        className="inline-flex items-center gap-1 font-mono text-[11px] text-amber-400/90 transition-colors hover:text-amber-300">
        <ChevronDown size={12} className={`transition-transform ${open ? "rotate-180" : ""}`} /> {open ? "Less" : label}
      </button>
      {open && <div className="mt-1.5 rounded-sm border border-line bg-panel2 p-2.5 text-[12px] leading-relaxed text-slate-300">{children}</div>}
    </div>
  );
}

export function Expand({ label = "Calculation details", children, testid }) {
  return <More label={label} testid={testid} className="mt-2">{children}</More>;
}

/** Page title row. A short description shows under the title; a long one folds behind ⓘ. */
export function PageHead({ title, sub, right }) {
  const [open, setOpen] = useState(false);
  const long = sub && textLen(sub) > 90;
  return (
    <div className="mb-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex min-w-0 items-center gap-2">
          <h1 className="truncate text-[19px] font-semibold tracking-tight text-slate-50">{title}</h1>
          {long && <InfoToggle open={open} onClick={() => setOpen(!open)} label="About this page" testid="page-info" />}
        </div>
        {right && <div className="flex flex-wrap items-center gap-2">{right}</div>}
      </div>
      {sub && (!long || open) && <p className={`mt-1 max-w-4xl text-[12.5px] leading-relaxed text-slate-400 ${long ? "rounded-sm border border-line bg-panel2/70 px-3 py-2" : ""}`}>{sub}</p>}
    </div>
  );
}

export function Empty({ children }) {
  return <div className="rounded-sm border border-dashed border-line p-4 text-center font-mono text-[11.5px] text-slate-500">{children}</div>;
}

/** Note: info notes show one line with "More"; warnings show two. */
export function Note({ children, tone = "slate", className = "" }) {
  const ref = useRef(null);
  const [open, setOpen] = useState(false);
  const [clamped, setClamped] = useState(false);
  useLayoutEffect(() => {
    const el = ref.current;
    if (el && !open) setClamped(el.scrollHeight > el.clientHeight + 2);
  }, [children, open]);
  const c = { plain: "border-transparent px-0 text-slate-500", slate: "border-line text-slate-400 bg-panel2/50", amber: "border-amber-500/40 text-amber-200/90 bg-amber-500/[0.06]", red: "border-red-500/45 text-red-200 bg-red-500/[0.07]", teal: "border-teal-500/40 text-teal-200 bg-teal-500/[0.06]" }[tone] || "";
  const clamp = tone === "slate" || tone === "plain" ? "line-clamp-1" : "line-clamp-2";
  if (children == null || children === "" || children === false) return null;
  return (
    <div className={`flex items-start gap-2 rounded-sm border px-2.5 py-1.5 ${tone === "plain" ? "text-[11.5px]" : "text-[12px]"} leading-relaxed ${c} ${className}`}>
      <div ref={ref} className={`min-w-0 flex-1 ${open ? "" : clamp}`}>{children}</div>
      {(clamped || open) && (
        <button type="button" onClick={() => setOpen(!open)} className="shrink-0 font-mono text-[10.5px] text-amber-400/90 hover:text-amber-300">{open ? "less" : "more"}</button>
      )}
    </div>
  );
}

/** Signed money / number with P&L colour. */
export function Num({ v, d = 2, money = true, className = "" }) {
  if (v === null || v === undefined || Number.isNaN(v)) return <span className={`text-slate-500 ${className}`}>—</span>;
  const s = `${v > 0 ? "+" : v < 0 ? "−" : ""}${money ? "$" : ""}${Math.abs(v).toLocaleString("en-US", { minimumFractionDigits: d, maximumFractionDigits: d })}`;
  return <span className={`font-mono ${v > 0 ? "text-teal-300" : v < 0 ? "text-red-300" : "text-slate-300"} ${className}`}>{s}</span>;
}

/** Segmented tabs (terminal style). items: [[key, label]] */
export function Tabs({ items, value, onChange, testid }) {
  return (
    <div data-testid={testid} className="inline-flex flex-wrap overflow-hidden rounded-sm border border-line bg-panel2">
      {items.map(([k, label]) => (
        <button key={k} type="button" onClick={() => onChange(k)} data-testid={`${testid || "tab"}-${k}`}
          className={`border-r border-line px-3 py-1.5 text-[12px] font-medium last:border-r-0 transition-colors ${value === k ? "bg-amber-500/15 text-amber-300" : "text-slate-400 hover:bg-raised hover:text-slate-100"}`}>
          {label}
        </button>
      ))}
    </div>
  );
}

// Render long tables a page at a time so big journals stay fast. Returns [visibleRows, footer].
export function usePaged(rows, step = 100, resetKey = "") {
  const [count, setCount] = useState(step);
  useEffect(() => { setCount(step); }, [resetKey, step]);
  const list = rows || [];
  const shown = list.length > count ? list.slice(0, count) : list;
  let footer = null;
  if (list.length > step) {
    footer = (
      <div className="flex flex-wrap items-center justify-between gap-2 border-t border-line px-2 py-1.5 font-mono text-[11px] text-slate-500" data-testid="pager">
        <span>Showing {shown.length.toLocaleString("en-US")} of {list.length.toLocaleString("en-US")} rows</span>
        <span className="flex gap-3">
          {list.length > count && <button type="button" onClick={() => setCount(count + step)} className="text-amber-400 hover:text-amber-300">Show {Math.min(step, list.length - count)} more</button>}
          {list.length > count && <button type="button" onClick={() => setCount(list.length)} className="text-slate-400 hover:text-slate-200">Show all</button>}
          {list.length <= count && <button type="button" onClick={() => setCount(step)} className="text-slate-400 hover:text-slate-200">Show first {step}</button>}
        </span>
      </div>
    );
  }
  return [shown, footer];
}
