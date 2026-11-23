import React from "react";

const cls = "w-full rounded-sm border border-line bg-panel2 px-2 py-1 font-mono text-[12.5px] text-slate-100 outline-none transition-colors placeholder:text-slate-600 hover:border-line2 focus:border-amber-500/70 disabled:opacity-60";

export function Field({ label, hint, children }) {
  return (
    <label className="block">
      <span className="font-mono text-[10.5px] text-slate-400">{label}</span>
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
  const t = { gold: "border border-amber-500 bg-amber-500 text-[#080a0d] hover:bg-amber-400", ghost: "border border-line bg-panel2 text-slate-200 hover:border-line2 hover:bg-raised", red: "border border-red-500/50 bg-red-500/[0.06] text-red-300 hover:bg-red-500/15", teal: "border border-teal-500/45 bg-teal-500/[0.06] text-teal-300 hover:bg-teal-500/15" }[tone];
  return <button {...p} className={`inline-flex items-center justify-center gap-1.5 rounded-sm px-2.5 py-1 text-[12px] font-semibold transition-colors disabled:cursor-not-allowed disabled:opacity-45 ${t} ${p.className || ""}`}>{children}</button>;
}
