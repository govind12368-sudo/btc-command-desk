import React, { useEffect, useState } from "react";
import { Sun, Moon } from "lucide-react";
import { DevCredit, FreeBadge, SiteFooter } from "@/components/brand/Branding";
import Logo from "@/components/brand/Logo";
import { APP_NAME } from "@/config/brand";

export function useAuthTheme() {
  const [theme, setTheme] = useState(() => localStorage.getItem("desk-auth-theme") || "dark");
  useEffect(() => { localStorage.setItem("desk-auth-theme", theme); }, [theme]);
  return [theme, setTheme];
}

export default function AuthShell({ title, sub, children, wide = false }) {
  const [theme, setTheme] = useAuthTheme();
  const light = theme === "light";
  return (
    <div data-testid="auth-shell" className={`auth-bg relative min-h-screen overflow-hidden ${light ? "auth-light text-slate-800" : "text-slate-100"}`}>
      <div className="auth-orb auth-orb-a" /><div className="auth-orb auth-orb-b" /><div className="auth-orb auth-orb-c" />
      <header className="relative z-10 flex items-center justify-between gap-3 px-4 py-4 sm:px-8">
        <div className="flex items-center gap-2.5">
          <Logo size={38} />
          <div className="leading-tight">
            <div className="text-[15px] font-bold tracking-tight">{APP_NAME}</div>
            <DevCredit light={light} className="text-[11px] sm:text-xs" />
          </div>
        </div>
        <button data-testid="theme-toggle" onClick={() => setTheme(light ? "dark" : "light")} aria-label="Toggle light or dark mode"
          className={`rounded-full border p-2 transition-colors ${light ? "border-slate-300 bg-white/70 text-slate-700 hover:text-amber-600" : "border-[#1e2a4a] bg-[#0b132b]/60 text-slate-300 hover:text-amber-300"}`}>
          {light ? <Moon size={16} /> : <Sun size={16} />}
        </button>
      </header>
      <main className="relative z-10 flex justify-center px-4 pb-4 pt-2 sm:pt-6">
        <div className={`auth-card w-full ${wide ? "max-w-[520px]" : "max-w-[440px]"} ${light ? "auth-card-light" : ""}`} data-testid="auth-card">
          <div className="mb-5 flex justify-center"><FreeBadge light={light} /></div>
          <h1 className="text-center text-2xl font-bold tracking-tight sm:text-[28px]">{title}</h1>
          {sub && <p className={`mt-1.5 text-center text-sm ${light ? "text-slate-600" : "text-slate-400"}`}>{sub}</p>}
          <div className="mt-6">{typeof children === "function" ? children(light) : children}</div>
        </div>
      </main>
      <div className="relative z-10"><SiteFooter light={light} /></div>
    </div>
  );
}

export function AuthError({ children }) {
  if (!children) return null;
  return <div role="alert" data-testid="auth-error" className="rounded-lg border border-red-500/40 bg-red-500/10 px-3 py-2 text-[13px] text-red-500">{children}</div>;
}

export function AuthOk({ children }) {
  if (!children) return null;
  return <div role="status" data-testid="auth-success" className="rounded-lg border border-teal-500/40 bg-teal-500/10 px-3 py-2 text-[13px] text-teal-500">{children}</div>;
}

export function Spinner() {
  return <span className="inline-block h-4 w-4 animate-spin rounded-full border-2 border-current border-r-transparent align-[-2px]" />;
}

export function AuthLabel({ children, light }) {
  return <label className={`mb-1.5 block text-[12.5px] font-medium ${light ? "text-slate-700" : "text-slate-300"}`}>{children}</label>;
}
