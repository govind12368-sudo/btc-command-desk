import React, { useEffect, useState } from "react";
import { NavLink, Outlet, useLocation, Link } from "react-router-dom";
import { LayoutDashboard, Activity, Zap, TrendingUp, Columns3, Calculator, Newspaper, BookOpen, ShieldCheck, Settings, Stethoscope, Lightbulb, Menu, X, RefreshCw, LogOut, WifiOff, UserCircle, Users, ChevronDown } from "lucide-react";
import { DevCredit, SiteFooter } from "@/components/brand/Branding";
import Logo from "@/components/brand/Logo";
import { APP_NAME, APP_SHORT, APP_TAGLINE } from "@/config/brand";
import { useDesk } from "@/context/DeskContext";
import { Chip } from "./ui";
import { ago } from "@/lib/fmt";

export const ADMIN_ONLY = new Set(["/settings", "/audit", "/upgrades", "/account", "/users"]);

export const NAV = [
  ["/", "BTC Dashboard", LayoutDashboard], ["/market", "Live Market", Activity], ["/scalping", "Scalping Signal", Zap],
  ["/swing", "Swing Signal", TrendingUp], ["/pillars", "Five Pillars", Columns3], ["/planner", "Trade Planner", Calculator],
  ["/news", "News & Events", Newspaper], ["/journal", "Trade Journal", BookOpen], ["/validation", "Trade Validation", ShieldCheck],
  ["/settings", "Settings", Settings], ["/audit", "System Audit", Stethoscope], ["/upgrades", "Upgrade Requests", Lightbulb],
];

function NavList({ onNav }) {
  const { isAdmin } = useDesk();
  const items = [...NAV, ["/account", "Account Settings", UserCircle], ["/users", "User Management", Users]].filter(([to]) => isAdmin || !ADMIN_ONLY.has(to));
  return (
    <nav className="flex flex-col gap-0.5 p-3">
      {items.map(([to, label, Icon]) => (
        <NavLink key={to} to={to} end={to === "/"} onClick={onNav} data-testid={`nav-${label.toLowerCase().replace(/[^a-z]+/g, "-")}`}
          className={({ isActive }) => `flex items-center gap-3 rounded-md px-3 py-2 text-sm transition-colors ${isActive ? "bg-amber-500/10 text-amber-300 border-l-2 border-amber-400" : "text-slate-400 hover:bg-[#131c35] hover:text-slate-100 border-l-2 border-transparent"}`}>
          <Icon size={16} /> {label}
        </NavLink>
      ))}
    </nav>
  );
}

function SecurityBanner() {
  const { user, state } = useDesk();
  if (user?.role !== "admin") return null;
  const warn = state?.config_warnings || [];
  if (user?.totp_enabled && !warn.length) return null;
  return (
    <div className="mb-4 space-y-2" data-testid="security-banner">
      {!user?.totp_enabled && <div className="rounded border border-red-500/40 bg-red-500/10 px-4 py-2 text-sm text-red-200">Two-factor authentication is <b>off</b> for this admin account. <Link to="/audit" className="underline">Turn it on in System Audit → Security</Link>.</div>}
      {warn.map((w, i) => <div key={i} className="rounded border border-amber-500/40 bg-amber-500/10 px-4 py-2 text-sm text-amber-200">{w}</div>)}
    </div>
  );
}

function Clock() {
  const [t, setT] = useState(new Date());
  useEffect(() => { const i = setInterval(() => setT(new Date()), 1000); return () => clearInterval(i); }, []);
  return <span data-testid="ist-clock" className="font-mono text-sm text-amber-300">{t.toLocaleTimeString("en-GB", { timeZone: "Asia/Kolkata", hour12: false })} <span className="text-slate-500 text-xs">IST</span></span>;
}

function Brand() {
  return (
    <div className="flex items-center gap-2">
      <Logo size={34} />
      <div className="leading-tight">
        <div className="text-[15px] font-bold tracking-tight text-slate-50">{APP_NAME}</div>
        <div className="font-mono text-[10px] text-slate-500">{APP_TAGLINE}</div>
      </div>
    </div>
  );
}

function UserMenu({ user, logout }) {
  const [open, setOpen] = useState(false);
  const loc = useLocation();
  useEffect(() => setOpen(false), [loc.pathname]);
  return (
    <div className="relative">
      <button data-testid="user-menu-button" onClick={() => setOpen(!open)} className="flex items-center gap-1.5 rounded-full border border-[#1e2a4a] py-1 pl-1 pr-2 text-slate-200 hover:border-amber-500/50 transition-colors">
        <span className="grid h-6 w-6 place-items-center rounded-full bg-amber-500/15 text-[11px] font-bold text-amber-300">{(user?.name || user?.username || "U").slice(0, 1).toUpperCase()}</span>
        <span className="hidden sm:inline max-w-[110px] truncate text-xs">{user?.username || user?.email}</span><ChevronDown size={13} />
      </button>
      {open && (
        <div data-testid="user-menu" className="menu-pop absolute right-0 mt-2 w-56 rounded-lg border border-[#1e2a4a] bg-[#0f172a] p-1.5 shadow-2xl">
          <div className="px-2.5 py-2 text-xs"><div className="truncate font-semibold text-slate-100">{user?.name}</div><div className="truncate text-slate-500">{user?.email}</div>
            <div className="mt-1 font-mono text-[10px] uppercase text-amber-400/80">{user?.role === "admin" ? "Administrator" : "User · read only"}</div></div>
          {user?.role === "admin" && <Link to="/account" data-testid="menu-account" className="flex items-center gap-2 rounded px-2.5 py-2 text-sm text-slate-300 hover:bg-[#131c35]"><UserCircle size={15} /> Account Settings</Link>}
          {user?.role === "admin" && <Link to="/users" data-testid="menu-users" className="flex items-center gap-2 rounded px-2.5 py-2 text-sm text-slate-300 hover:bg-[#131c35]"><Users size={15} /> User Management</Link>}
          <button data-testid="logout-button" onClick={logout} className="flex w-full items-center gap-2 rounded px-2.5 py-2 text-sm text-red-300 hover:bg-red-500/10"><LogOut size={15} /> Sign out</button>
        </div>
      )}
    </div>
  );
}

export default function Layout() {
  const { state, conn, online, refresh, logout, user } = useDesk();
  const [open, setOpen] = useState(false);
  const loc = useLocation();
  useEffect(() => setOpen(false), [loc.pathname]);
  const ws = state?.ws?.status;
  // "partial" = working with incomplete coverage (e.g. the release calendar); only real outages degrade.
  const bad = (state?.sources || []).filter((s) => s.status !== "ok" && s.status !== "partial").length;
  const connStatus = !online ? "OFFLINE" : conn.ok === false ? "DISCONNECTED" : ws === "connected" ? (bad ? "DEGRADED" : "CONNECTED") : "RECONNECTING";
  const connTone = { CONNECTED: "PASS", DEGRADED: "WAIT", RECONNECTING: "WAIT", DISCONNECTED: "BLOCKED", OFFLINE: "BLOCKED" }[connStatus];
  return (
    <div className="min-h-screen">
      <aside className="fixed inset-y-0 left-0 z-30 hidden w-[250px] flex-col border-r border-[#1e2a4a] bg-[#0b132b] lg:flex">
        <div className="h-[60px] flex items-center px-4 border-b border-[#1e2a4a]"><Brand /></div>
        <div className="flex-1 overflow-y-auto scroll-thin"><NavList /></div>
        <div className="border-t border-[#1e2a4a] p-3 text-xs text-slate-500">
          <div className="truncate" data-testid="sidebar-user">{user?.email}</div>
          <div className="font-mono text-[10px] uppercase text-amber-400/80">{user?.role === "admin" ? "admin · full edit" : "user · read only"}</div>
        </div>
      </aside>
      {open && (
        <div className="fixed inset-0 z-50 lg:hidden">
          <div className="absolute inset-0 bg-black/60 backdrop-blur-md" onClick={() => setOpen(false)} />
          <aside data-testid="mobile-drawer" className="absolute inset-y-0 left-0 w-[270px] overflow-y-auto bg-[#0b132b] border-r border-[#1e2a4a]">
            <div className="flex h-[60px] items-center justify-between px-4 border-b border-[#1e2a4a]"><Brand /><button data-testid="close-drawer" onClick={() => setOpen(false)}><X size={18} /></button></div>
            <NavList onNav={() => setOpen(false)} />
          </aside>
        </div>
      )}
      <header className="fixed top-0 right-0 left-0 lg:left-[250px] z-40 h-[60px] flex items-center justify-between gap-2 border-b border-[#1e2a4a] bg-[#0b132b]/90 px-3 sm:px-5 backdrop-blur-md">
        <div className="flex items-center gap-3">
          <button className="lg:hidden" data-testid="open-drawer" onClick={() => setOpen(true)}><Menu size={20} /></button>
          <div className="lg:hidden flex items-center gap-1.5"><Logo size={24} /><span className="whitespace-nowrap text-sm font-bold">{APP_SHORT}</span></div>
          <Clock />
          <DevCredit className="hidden xl:inline border-l border-[#1e2a4a] pl-3 text-[12.5px] tracking-wide" />
        </div>
        <div className="flex items-center gap-2 sm:gap-3">
          {state?.price ? <span className="hidden sm:inline font-mono text-sm font-semibold text-slate-100" data-testid="header-price">₿ ${Number(state.price).toLocaleString("en-US", { maximumFractionDigits: 1 })}</span> : null}
          {user?.role === "admin" && state && (
            <Link to="/settings" data-testid="header-alert-mode" title="Alert mode (Settings → Signal alerts)"
              className={`hidden md:inline-flex items-center gap-1 rounded-full border px-2 py-0.5 font-mono text-[10.5px] font-semibold ${state.alert_mode_armed ? "border-teal-500/50 text-teal-300" : "border-amber-500/40 text-amber-300"}`}>
              <span className={`h-1.5 w-1.5 rounded-full ${state.alert_mode_armed ? "bg-teal-400" : "bg-amber-400"}`} />ALERTS {state.alert_mode_armed ? "ON" : "OFF"}
            </Link>
          )}
          <span className="hidden md:inline font-mono text-[11px] text-slate-500" data-testid="last-update">updated {ago(conn.lastOk)}</span>
          <Chip status={connTone} label={connStatus} testid="connection-status" />
          <button data-testid="refresh-button" onClick={refresh} className="rounded border border-[#1e2a4a] p-1.5 text-slate-300 hover:border-amber-500/50 hover:text-amber-300 transition-colors" title="Refresh now">
            <RefreshCw size={15} className={conn.loading ? "animate-spin" : ""} />
          </button>
          <UserMenu user={user} logout={logout} />
        </div>
      </header>
      <main className="pt-[60px] lg:pl-[250px]">
        {(!online || conn.ok === false) && (
          <div data-testid="offline-banner" className="flex items-center gap-2 bg-red-950/70 border-b border-red-500/40 px-4 py-2 text-xs text-red-200">
            <WifiOff size={14} /> {online ? "Server unreachable." : "You are offline."} Values on screen are the last received and are NOT live. Do not trade from them.
          </div>
        )}
        <div className="xl:hidden border-b border-[#1e2a4a] bg-[#0b132b]/70 px-3 py-1.5 text-center text-[11.5px]"><DevCredit /></div>
        <div key={loc.pathname} className="page-enter mx-auto max-w-[1600px] p-3 sm:p-5 lg:p-6"><SecurityBanner /><Outlet /></div>
        <SiteFooter />
      </main>
    </div>
  );
}
