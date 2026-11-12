import React from "react";

const cls = "w-full rounded border border-[#1e2a4a] bg-[#0f172a] px-2.5 py-1.5 font-mono text-[13px] text-slate-100 outline-none focus:border-amber-500/60 disabled:opacity-60";

export function Field({ label, hint, children }) {
  return (
    <label className="block">
      <span className="text-[11.5px] text-slate-400">{label}</span>
      <div className="mt-1">{children}</div>
      {hint && <span className="mt-0.5 block text-[10.5px] text-slate-500">{hint}</span>}
    </label>
  );
}

export const Input = (p) => <input {...p} className={`${cls} ${p.className || ""}`} />;
export const Area = (p) => <textarea {...p} className={`${cls} min-h-[90px] ${p.className || ""}`} />;
export const Select = ({ options, ...p }) => (
  <select {...p} className={cls}>{options.map((o) => <option key={o.value ?? o} value={o.value ?? o}>{o.label ?? o}</option>)}</select>
);

export function Btn({ children, tone = "gold", ...p }) {
  const t = { gold: "bg-amber-500 text-[#0b132b] hover:bg-amber-400", ghost: "border border-[#1e2a4a] text-slate-200 hover:border-amber-500/50", red: "border border-red-500/40 text-red-300 hover:bg-red-500/10", teal: "border border-teal-500/40 text-teal-300 hover:bg-teal-500/10" }[tone];
  return <button {...p} className={`rounded px-3 py-1.5 text-xs font-semibold transition-colors disabled:opacity-50 ${t} ${p.className || ""}`}>{children}</button>;
}
