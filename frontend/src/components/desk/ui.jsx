import React, { useState } from "react";
import { ChevronDown } from "lucide-react";

const STYLES = {
  teal: "bg-teal-950/60 text-teal-300 border-teal-500/40 shadow-[0_0_12px_rgba(20,184,166,0.18)]",
  amber: "bg-amber-950/60 text-amber-300 border-amber-500/40",
  indigo: "bg-indigo-950/60 text-indigo-300 border-indigo-500/40",
  red: "bg-red-950/60 text-red-300 border-red-500/40 shadow-[0_0_12px_rgba(239,68,68,0.15)]",
  slate: "bg-slate-800/60 text-slate-300 border-slate-600/40",
  gray: "bg-gray-900/80 text-gray-500 border-gray-700/50",
};
const MAP = {
  PASS: "teal", OK: "teal", BUY: "teal", LONG: "teal", "TARGET FIRST": "teal", RECOVERED: "teal", CONNECTED: "teal", AGREE: "teal", ACTIVE: "teal", REVIEWED: "teal", UP: "teal",
  WAIT: "amber", "PLAN ONLY": "amber", "READY TO CAPTURE": "amber", PENDING: "amber", PAUSED: "amber", CHECKING: "amber", "COOLING DOWN": "amber", NEUTRAL: "slate", MIXED: "slate",
  REVIEW: "indigo", MONITOR: "indigo", "SAME CANDLE REVIEW": "indigo", "CAPTURE CANDLE REVIEW": "indigo", UNREVIEWED: "indigo", CONSUMED: "indigo",
  BLOCKED: "red", FAIL: "red", SELL: "red", SHORT: "red", "STOP FIRST": "red", ERROR: "red", CONFLICT: "red", INVALIDATED: "red", DOWN: "red",
  "STILL NEEDS ATTENTION": "red", "SOURCE ERROR": "red", "INVALID SNAPSHOT": "red", CRITICAL: "red", HIGH: "amber", MEDIUM: "indigo", LOW: "slate",
  STALE: "slate", EXPIRED: "slate", CANCELLED: "slate", IGNORED: "slate", RECONNECTING: "amber",
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
    <span data-testid={testid} className={`inline-flex items-center gap-1 rounded border px-2 py-0.5 font-mono text-[10.5px] font-semibold tracking-wide whitespace-nowrap ${st} ${className}`}>
      {label || statusLabel(status)}
    </span>
  );
}

export function Card({ title, right, children, className = "", testid, sub }) {
  return (
    <section data-testid={testid} className={`desk-card p-4 sm:p-5 ${className}`}>
      {(title || right) && (
        <header className="mb-3 flex flex-wrap items-start justify-between gap-2">
          <div>
            {title && <h3 className="text-[15px] font-semibold text-slate-100 tracking-tight">{title}</h3>}
            {sub && <p className="mt-0.5 font-mono text-[11px] text-slate-500">{sub}</p>}
          </div>
          {right}
        </header>
      )}
      {children}
    </section>
  );
}

export function KV({ k, v, mono = true, testid, className = "" }) {
  return (
    <div className={`flex items-baseline justify-between gap-3 py-1 border-b border-[#1e2a4a]/50 last:border-0 ${className}`}>
      <span className="text-xs text-slate-400">{k}</span>
      <span data-testid={testid} className={`${mono ? "font-mono" : ""} text-[13px] text-slate-100 text-right`}>{v}</span>
    </div>
  );
}

export function Stat({ label, value, sub, testid, valueClass = "" }) {
  return (
    <div className="desk-card px-4 py-3">
      <div className="font-mono text-[10.5px] uppercase tracking-wider text-slate-500">{label}</div>
      <div data-testid={testid} className={`mt-1 font-mono text-xl sm:text-2xl font-bold text-slate-50 ${valueClass}`}>{value}</div>
      {sub && <div className="mt-0.5 text-[11px] text-slate-500">{sub}</div>}
    </div>
  );
}

export function Expand({ label = "Calculation details", children, testid }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="mt-2">
      <button data-testid={testid} onClick={() => setOpen(!open)} className="flex items-center gap-1 text-[11px] font-mono text-amber-400/90 hover:text-amber-300 transition-colors">
        <ChevronDown size={13} className={`transition-transform ${open ? "rotate-180" : ""}`} /> {label}
      </button>
      {open && <div className="mt-2 rounded border border-[#1e2a4a] bg-[#0f172a] p-3 text-xs text-slate-300">{children}</div>}
    </div>
  );
}

export function PageHead({ title, sub, right }) {
  return (
    <div className="mb-5 flex flex-wrap items-end justify-between gap-3">
      <div>
        <h1 className="text-2xl sm:text-3xl font-extrabold tracking-tight text-slate-50">{title}</h1>
        {sub && <p className="mt-1 max-w-3xl text-sm text-slate-400">{sub}</p>}
      </div>
      {right}
    </div>
  );
}

export function Empty({ children }) {
  return <div className="rounded border border-dashed border-[#1e2a4a] p-4 text-center text-xs text-slate-500">{children}</div>;
}

export function Note({ children, tone = "slate" }) {
  const c = { slate: "border-slate-700/60 text-slate-400", amber: "border-amber-500/40 text-amber-200/90 bg-amber-950/20", red: "border-red-500/40 text-red-200 bg-red-950/20" }[tone];
  return <div className={`rounded border px-3 py-2 text-xs ${c}`}>{children}</div>;
}
