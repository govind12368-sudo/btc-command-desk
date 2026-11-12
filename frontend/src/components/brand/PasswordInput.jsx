import React, { useState } from "react";
import { Eye, EyeOff } from "lucide-react";

export const RULES = [
  [(p) => p.length >= 10, "10+ characters"],
  [(p) => /[a-z]/.test(p), "lowercase"],
  [(p) => /[A-Z]/.test(p), "uppercase"],
  [(p) => /\d/.test(p), "number"],
  [(p) => /[^A-Za-z0-9]/.test(p), "symbol"],
];
export const strongEnough = (p) => RULES.every(([f]) => f(p));

export function PasswordInput({ value, onChange, testid, placeholder, light, autoComplete = "current-password" }) {
  const [show, setShow] = useState(false);
  return (
    <div className="relative">
      <input type={show ? "text" : "password"} value={value} onChange={(e) => onChange(e.target.value)} data-testid={testid} placeholder={placeholder} autoComplete={autoComplete} required
        className={`auth-input pr-10 ${light ? "auth-input-light" : ""}`} />
      <button type="button" onClick={() => setShow(!show)} data-testid={`${testid}-toggle`} aria-label={show ? "Hide password" : "Show password"}
        className={`absolute right-2.5 top-1/2 -translate-y-1/2 p-1 ${light ? "text-slate-500 hover:text-slate-800" : "text-slate-400 hover:text-amber-300"} transition-colors`}>
        {show ? <EyeOff size={16} /> : <Eye size={16} />}
      </button>
    </div>
  );
}

export function StrengthMeter({ value, light }) {
  const n = RULES.filter(([f]) => f(value)).length;
  const col = n <= 2 ? "bg-red-500" : n <= 4 ? "bg-amber-500" : "bg-teal-500";
  return (
    <div data-testid="password-strength" className="mt-1.5">
      <div className="flex gap-1">{RULES.map((_, i) => <span key={i} className={`h-1 flex-1 rounded-full transition-colors duration-300 ${i < n ? col : light ? "bg-slate-200" : "bg-[#1e2a4a]"}`} />)}</div>
      <div className="mt-1 flex flex-wrap gap-x-3 text-[10.5px]">
        {RULES.map(([f, l]) => <span key={l} className={f(value) ? "text-teal-500" : light ? "text-slate-500" : "text-slate-500"}>{f(value) ? "✓" : "•"} {l}</span>)}
      </div>
    </div>
  );
}
