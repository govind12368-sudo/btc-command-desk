import React, { Suspense, useEffect, useMemo, useRef, useState } from "react";
import { NavLink, Outlet, useLocation, Link, Navigate } from "react-router-dom";
import { BadgeCheck,
  LayoutDashboard, Activity, Zap, TrendingUp, Columns3, Radar, Telescope, Newspaper, BookOpen, ShieldCheck, Settings, Stethoscope, Lightbulb, Menu, X,
  RefreshCw, LogOut, WifiOff, UserCircle, Users, ChevronDown, FlaskConical, Bot, TrendingDown, Layers, Trophy, Gem, Server, Target, HeartPulse,
  PanelLeftClose, PanelLeftOpen, SlidersHorizontal, Eye, EyeOff, Lock, Globe, PieChart, Microscope } from "lucide-react";
import { SiteFooter } from "@/components/brand/Branding";
import Logo from "@/components/brand/Logo";
import { APP_NAME, APP_SHORT } from "@/config/brand";
import { useDesk } from "@/context/DeskContext";
import { usePulse } from "@/lib/live";
import { Chip } from "./ui";
import { ago } from "@/lib/fmt";
import { toast } from "sonner";

export const ADMIN_ONLY = new Set(["/real-account", "/straddle-bot", "/auto-trade", "/settings", "/audit", "/upgrades", "/account", "/users", "/server", "/data-health"]);

// Menu, grouped the way a trading desk works: look at the market, read signals, run strategies, trade, run the system.
// Site name with "BTC" in the brand gold (the plain name if it has no "BTC" in it).
function Brand() {
  const i = APP_NAME.indexOf("BTC");
  if (i < 0) return <>{APP_NAME}</>;
  return <>{APP_NAME.slice(0, i)}<span className="text-amber-400">BTC</span>{APP_NAME.slice(i + 3)}</>;
}

export const NAV_GROUPS = [
  ["Market", [["/", "BTC Dashboard", LayoutDashboard], ["/market", "Live Market", Activity], ["/scanner", "BTC Scanner", Radar], ["/intel", "Market Intelligence", Telescope], ["/news", "News & Events", Newspaper]]],
  ["Signals", [["/scalping", "Scalping Signal", Zap], ["/swing", "Swing Signal", TrendingUp], ["/pillars", "Five Pillars", Columns3], ["/validation", "Trade Validation", ShieldCheck]]],
  ["Strategies", [["/research", "Research & Edge", Microscope], ["/plan-a", "Plan A – Live", FlaskConical], ["/flush-short", "Flush Short", TrendingDown], ["/candle-runs", "Candle Runs", Layers], ["/delta-top3", "Delta Top 3", Trophy],
    ["/elite-4-edge", "Elite 4 Edge", Gem], ["/option-picks", "Option Picks", Target], ["/real-account", "Real Account Tested", BadgeCheck], ["/straddle-bot", "Straddle Bot", Bot], ["/observer", "Strategy Observer", Telescope]]],
  ["Trading", [["/fund-manager", "Fund Manager", PieChart], ["/auto-trade", "Auto-Trade", Bot], ["/journal", "Trade Journal", BookOpen], ["/regime", "Regime Journal", BookOpen]]],
  ["System", [["/data-health", "Data Health", HeartPulse], ["/server", "Server", Server], ["/settings", "Settings", Settings], ["/audit", "System Audit", Stethoscope],
    ["/upgrades", "Upgrade Requests", Lightbulb], ["/account", "Account Settings", UserCircle], ["/users", "User Management", Users]]],
];
export const NAV = NAV_GROUPS.flatMap(([, items]) => items);

const lsGet = (k, d) => { try { const v = localStorage.getItem(k); return v === null ? d : JSON.parse(v); } catch (e) { return d; } };
const lsSet = (k, v) => { try { localStorage.setItem(k, JSON.stringify(v)); } catch (e) { /* storage blocked: keep in memory only */ } };

function useMenu() {
  const { isAdmin, ui } = useDesk();
  return useMemo(() => {
    const g = new Set(ui?.hidden_global || []), mine = new Set(ui?.hidden_user || []);
    return NAV_GROUPS.map(([name, items]) => [name, items.filter(([to]) => (isAdmin || !ADMIN_ONLY.has(to)) && !mine.has(to) && (isAdmin || !g.has(to)))])
      .filter(([, items]) => items.length);
  }, [isAdmin, ui]);
}

function NavList({ onNav, rail }) {
  const groups = useMenu();
  const { ui, isAdmin } = useDesk();
  const g = new Set(ui?.hidden_global || []);
  return (
    <nav className={`flex flex-col ${rail ? "gap-0.5 px-1.5 py-2" : "px-2 py-2"}`}>
      {groups.map(([name, items]) => (
        <div key={name} className={rail ? "border-b border-line/60 pb-1 last:border-0" : "mb-2"}>
          {!rail && <div className="px-2 pb-1 pt-1.5 font-mono text-[10px] tracking-wide text-slate-500">{name}</div>}
          {items.map(([to, label, Icon]) => (
            <NavLink key={to} to={to} end={to === "/"} onClick={onNav} title={rail ? label : undefined} data-testid={`nav-${label.toLowerCase().replace(/[^a-z]+/g, "-")}`}
              className={({ isActive }) => `group relative flex items-center gap-2.5 rounded-sm ${rail ? "justify-center px-0 py-2" : "px-2 py-[5px]"} text-[13px] transition-colors ${isActive
                ? "bg-amber-500/[0.09] text-amber-300 before:absolute before:inset-y-1 before:left-0 before:w-[2px] before:bg-amber-400"
                : "text-slate-400 hover:bg-raised hover:text-slate-100"}`}>
              <Icon size={15} className="shrink-0" />
              {!rail && <span className="truncate">{label}</span>}
              {!rail && isAdmin && g.has(to) && <span title="Hidden for users" className="ml-auto"><EyeOff size={12} className="text-slate-600" /></span>}
            </NavLink>
          ))}
        </div>
      ))}
    </nav>
  );
}

/** Menu editor: each page can be shown/hidden in my menu; an admin can also hide it for every user. */
function MenuEditor({ onClose }) {
  const { ui, isAdmin, setHiddenUser, setHiddenGlobal } = useDesk();
  const [busy, setBusy] = useState(false);
  const mine = new Set(ui?.hidden_user || []), glob = new Set(ui?.hidden_global || []), locked = new Set(ui?.locked || ["/"]);
  const toggle = async (set, fn, to) => {
    const next = new Set(set);
    next.has(to) ? next.delete(to) : next.add(to);
    setBusy(true);
    try { await fn([...next]); } catch (e) { toast.error("Could not save the menu"); } finally { setBusy(false); }
  };
  const showAll = async () => { setBusy(true); try { await setHiddenUser([]); } finally { setBusy(false); } };
  return (
    <div className="fixed inset-0 z-[60] flex justify-end" data-testid="menu-editor">
      <div className="absolute inset-0 bg-black/60" onClick={onClose} />
      <aside className="relative flex h-full w-[380px] max-w-full flex-col border-l border-line bg-chrome">
        <div className="flex items-center justify-between border-b border-line px-4 py-2.5">
          <div><div className="text-[14px] font-semibold text-slate-100">Customize menu</div><div className="text-[11.5px] text-slate-500">Hidden pages are not deleted; show them again any time.</div></div>
          <button onClick={onClose} className="rounded-sm p-1 text-slate-400 hover:bg-raised hover:text-slate-100" aria-label="Close"><X size={16} /></button>
        </div>
        <div className="grid grid-cols-[1fr_auto_auto] items-center gap-x-3 border-b border-line px-4 py-1.5 font-mono text-[10.5px] text-slate-500">
          <span>Page</span><span className="w-[70px] text-center">My menu</span>{isAdmin ? <span className="w-[70px] text-center">All users</span> : <span />}
        </div>
        <div className="flex-1 overflow-y-auto scroll-thin px-2 py-1">
          {NAV_GROUPS.map(([name, items]) => {
            const list = items.filter(([to]) => isAdmin || (!ADMIN_ONLY.has(to) && !glob.has(to)));
            if (!list.length) return null;
            return (
              <div key={name} className="mb-1">
                <div className="px-2 pb-0.5 pt-2 font-mono text-[10px] text-slate-500">{name}</div>
                {list.map(([to, label, Icon]) => (
                  <div key={to} className="grid grid-cols-[1fr_auto_auto] items-center gap-x-3 rounded-sm px-2 py-1 hover:bg-raised/60">
                    <span className="flex min-w-0 items-center gap-2 text-[13px] text-slate-200"><Icon size={14} className="shrink-0 text-slate-500" /><span className="truncate">{label}</span></span>
                    {locked.has(to) ? <span className="flex w-[70px] justify-center text-slate-600" title="Always shown"><Lock size={13} /></span> : (
                      <button disabled={busy} onClick={() => toggle(mine, setHiddenUser, to)} data-testid={`menu-mine-${to}`}
                        className={`flex w-[70px] items-center justify-center gap-1 rounded-sm border py-0.5 font-mono text-[10.5px] ${mine.has(to) ? "border-line text-slate-500" : "border-teal-500/40 text-teal-300"}`}>
                        {mine.has(to) ? <><EyeOff size={11} /> hidden</> : <><Eye size={11} /> shown</>}
                      </button>
                    )}
                    {isAdmin ? (locked.has(to) || ADMIN_ONLY.has(to) ? <span className="w-[70px] text-center font-mono text-[10px] text-slate-600">{locked.has(to) ? "always" : "admin only"}</span> : (
                      <button disabled={busy} onClick={() => toggle(glob, setHiddenGlobal, to)} data-testid={`menu-all-${to}`}
                        className={`flex w-[70px] items-center justify-center gap-1 rounded-sm border py-0.5 font-mono text-[10.5px] ${glob.has(to) ? "border-red-500/40 text-red-300" : "border-line text-slate-400"}`}>
                        {glob.has(to) ? <><EyeOff size={11} /> hidden</> : <><Globe size={11} /> visible</>}
                      </button>
                    )) : <span />}
                  </div>
                ))}
              </div>
            );
          })}
        </div>
        <div className="flex items-center justify-between border-t border-line px-4 py-2.5 text-[11.5px] text-slate-500">
          <span>{isAdmin ? "\"All users\" hides a page for every non-admin account." : "Only your own menu changes."}</span>
          <button onClick={showAll} disabled={busy || !mine.size} className="rounded-sm border border-line px-2 py-1 text-slate-300 hover:bg-raised disabled:opacity-40">Show all</button>
        </div>
      </aside>
    </div>
  );
}

function SecurityBanner() {
  const { user, state } = useDesk();
  if (user?.role !== "admin") return null;
  const warn = state?.config_warnings || [];
  if (user?.totp_enabled && !warn.length) return null;
  return (
    <div className="mb-3 space-y-1.5" data-testid="security-banner">
      {!user?.totp_enabled && <div className="rounded-sm border border-red-500/40 bg-red-500/[0.07] px-3 py-1.5 text-[12px] text-red-200">Two-factor authentication is <b>off</b> for this admin account. <Link to="/audit" className="underline">Turn it on in System Audit → Security</Link>.</div>}
      {warn.map((w, i) => <div key={i} className="rounded-sm border border-amber-500/40 bg-amber-500/[0.06] px-3 py-1.5 text-[12px] text-amber-200">{w}</div>)}
    </div>
  );
}

function Clock() {
  const [t, setT] = useState(new Date());
  useEffect(() => { const i = setInterval(() => setT(new Date()), 1000); return () => clearInterval(i); }, []);
  return <span data-testid="ist-clock" className="font-mono text-[12.5px] text-slate-300">{t.toLocaleTimeString("en-GB", { timeZone: "Asia/Kolkata", hour12: false })} <span className="text-[10.5px] text-slate-500">IST</span></span>;
}

/** Live ticker: BTCUSD last (push, flashes on change), 24h change, mark, funding, open interest. */
function Ticker() {
  const { state } = useDesk();
  const p = usePulse();
  const last = p?.price?.last || state?.price;
  const prev = useRef(last);
  const [flash, setFlash] = useState("");
  useEffect(() => {
    if (last && prev.current && last !== prev.current) {
      setFlash(last > prev.current ? "tick-up" : "tick-down");
      const t = setTimeout(() => setFlash(""), 650);
      prev.current = last;
      return () => clearTimeout(t);
    }
    prev.current = last;
    return undefined;
  }, [last]);
  const tk = state?.ticker || {};
  const open = Number(tk.open_24h);
  const chg = last && open ? (last / open - 1) * 100 : null;
  const f1 = (v) => (v || v === 0 ? Number(v).toLocaleString("en-US", { minimumFractionDigits: 1, maximumFractionDigits: 1 }) : "—");
  const cell = "hidden items-baseline gap-1.5 border-l border-line px-3 xl:flex";
  return (
    <div className="flex h-full items-center" data-testid="ticker">
      <div className="flex items-baseline gap-1.5 whitespace-nowrap px-1 sm:gap-2 sm:px-3">
        <span className="hidden font-mono text-[11px] text-slate-500 sm:inline">BTCUSD</span>
        <span data-testid="header-price" className={`rounded-sm px-1 font-mono text-[15px] font-semibold text-slate-50 ${flash}`}>{f1(last)}</span>
        {chg != null && <span className={`font-mono text-[12px] font-semibold ${chg >= 0 ? "text-teal-300" : "text-red-300"}`}>{chg >= 0 ? "▲" : "▼"} {Math.abs(chg).toFixed(2)}%</span>}
      </div>
      <div className={cell}><span className="font-mono text-[10.5px] text-slate-500">Mark</span><span className="font-mono text-[12.5px] text-slate-200">{f1(p?.price?.mark || tk.mark)}</span></div>
      <div className={cell}><span className="font-mono text-[10.5px] text-slate-500">24h H/L</span><span className="font-mono text-[12.5px] text-slate-200">{f1(tk.high_24h)} / {f1(tk.low_24h)}</span></div>
      {tk.funding_rate_pct_8h != null && <div className={cell}><span className="font-mono text-[10.5px] text-slate-500">Funding 8h</span><span className={`font-mono text-[12.5px] ${tk.funding_rate_pct_8h >= 0 ? "text-teal-300" : "text-red-300"}`}>{Number(tk.funding_rate_pct_8h).toFixed(4)}%</span></div>}
      {tk.oi_usd != null && <div className={`${cell} 2xl:flex`}><span className="font-mono text-[10.5px] text-slate-500">OI</span><span className="font-mono text-[12.5px] text-slate-200">${(tk.oi_usd / 1e6).toFixed(1)}M</span></div>}
    </div>
  );
}

function UserMenu({ user, logout, onCustomize }) {
  const [open, setOpen] = useState(false);
  const loc = useLocation();
  useEffect(() => setOpen(false), [loc.pathname]);
  const item = "flex items-center gap-2 rounded-sm px-2.5 py-1.5 text-[13px] text-slate-300 hover:bg-raised";
  return (
    <div className="relative">
      <button data-testid="user-menu-button" onClick={() => setOpen(!open)} className="flex h-7 items-center gap-1.5 rounded-sm border border-line pl-1 pr-1.5 text-slate-200 transition-colors hover:border-line2">
        <span className="grid h-5 w-5 place-items-center rounded-sm bg-amber-500/15 text-[11px] font-bold text-amber-300">{(user?.name || user?.username || "U").slice(0, 1).toUpperCase()}</span>
        <span className="hidden max-w-[110px] truncate text-[12px] sm:inline">{user?.username || user?.email}</span><ChevronDown size={13} />
      </button>
      {open && (
        <div data-testid="user-menu" className="menu-pop absolute right-0 mt-1.5 w-60 rounded-sm border border-line bg-chrome p-1 shadow-2xl">
          <div className="px-2.5 py-2 text-[12px]"><div className="truncate font-semibold text-slate-100">{user?.name}</div><div className="truncate text-slate-500">{user?.email}</div>
            <div className="mt-1 font-mono text-[10px] text-amber-400/80">{user?.role === "admin" ? "Administrator" : "User · read only"}</div></div>
          <button onClick={() => { setOpen(false); onCustomize(); }} data-testid="menu-customize" className={`${item} w-full`}><SlidersHorizontal size={14} /> Customize menu</button>
          {user?.role === "admin" && <Link to="/account" data-testid="menu-account" className={item}><UserCircle size={14} /> Account Settings</Link>}
          {user?.role === "admin" && <Link to="/users" data-testid="menu-users" className={item}><Users size={14} /> User Management</Link>}
          {user?.role === "admin" && <Link to="/data-health" data-testid="menu-data-health" className={item}><HeartPulse size={14} /> Data Health</Link>}
          {user?.role === "admin" && <Link to="/server" data-testid="menu-server" className={item}><Server size={14} /> Server</Link>}
          <button data-testid="logout-button" onClick={logout} className="flex w-full items-center gap-2 rounded-sm px-2.5 py-1.5 text-[13px] text-red-300 hover:bg-red-500/10"><LogOut size={14} /> Sign out</button>
        </div>
      )}
    </div>
  );
}

export default function Layout() {
  const { state, conn, online, refresh, logout, user, isAdmin, ui, streamOk } = useDesk();
  const [open, setOpen] = useState(false);
  const [rail, setRail] = useState(() => lsGet("vb-rail", false));
  const [editing, setEditing] = useState(false);
  const loc = useLocation();
  useEffect(() => setOpen(false), [loc.pathname]);
  const ws = state?.ws?.status;
  // "partial" = working with incomplete coverage; news headlines come from several redundant feeds, so news counts only when every feed is down.
  const srcs = state?.sources || [];
  const isBad = (s) => s.status !== "ok" && s.status !== "partial";
  const news = srcs.filter((s) => s.family === "news");
  const badList = srcs.filter((s) => s.family !== "news" && isBad(s)).map((s) => s.name);
  if (news.length && news.every(isBad)) badList.push("All news feeds");
  const bad = badList.length;
  const connTitle = bad ? `Data source problem: ${badList.join(", ")} (details on Data Health)` : "All data sources working";
  const connStatus = !online ? "OFFLINE" : conn.ok === false ? "DISCONNECTED" : ws === "connected" ? (bad ? "DEGRADED" : "CONNECTED") : "RECONNECTING";
  const connTone = { CONNECTED: "PASS", DEGRADED: "WAIT", RECONNECTING: "WAIT", DISCONNECTED: "BLOCKED", OFFLINE: "BLOCKED" }[connStatus];
  const W = rail ? 56 : 216;
  const hiddenForUsers = !isAdmin && (ui?.hidden_global || []).includes(loc.pathname);
  const toggleRail = () => { setRail(!rail); lsSet("vb-rail", !rail); };
  return (
    <div className="min-h-screen">
      <aside className="fixed inset-y-0 left-0 z-30 hidden flex-col border-r border-line bg-chrome lg:flex" style={{ width: W }}>
        <div className={`flex h-[44px] items-center border-b border-line ${rail ? "justify-center" : "gap-2 px-3"}`}>
          <Logo size={24} />
          {!rail && <div className="leading-none"><div className="whitespace-nowrap text-[13.5px] font-bold tracking-tight text-slate-50"><Brand /></div><div className="mt-0.5 font-mono text-[9.5px] text-slate-500">decision desk</div></div>}
        </div>
        <div className="flex-1 overflow-y-auto scroll-thin"><NavList rail={rail} /></div>
        <div className={`flex items-center border-t border-line ${rail ? "flex-col gap-1 py-2" : "justify-between px-2 py-1.5"}`}>
          <button onClick={() => setEditing(true)} title="Customize menu" data-testid="customize-menu" className="flex items-center gap-1.5 rounded-sm px-1.5 py-1 text-[11.5px] text-slate-400 hover:bg-raised hover:text-slate-100"><SlidersHorizontal size={14} />{!rail && "Customize"}</button>
          <button onClick={toggleRail} title={rail ? "Expand menu" : "Collapse menu"} data-testid="toggle-rail" className="rounded-sm p-1 text-slate-400 hover:bg-raised hover:text-slate-100">{rail ? <PanelLeftOpen size={15} /> : <PanelLeftClose size={15} />}</button>
        </div>
      </aside>
      {open && (
        <div className="fixed inset-0 z-50 lg:hidden">
          <div className="absolute inset-0 bg-black/60" onClick={() => setOpen(false)} />
          <aside data-testid="mobile-drawer" className="absolute inset-y-0 left-0 w-[260px] overflow-y-auto border-r border-line bg-chrome">
            <div className="flex h-[44px] items-center justify-between border-b border-line px-3"><div className="flex items-center gap-2"><Logo size={22} /><span className="whitespace-nowrap text-[13.5px] font-bold"><Brand /></span></div><button data-testid="close-drawer" onClick={() => setOpen(false)}><X size={18} /></button></div>
            <NavList onNav={() => setOpen(false)} />
            <button onClick={() => { setOpen(false); setEditing(true); }} className="mx-2 mb-3 flex items-center gap-1.5 rounded-sm px-2 py-1 text-[12px] text-slate-400 hover:bg-raised"><SlidersHorizontal size={14} /> Customize menu</button>
          </aside>
        </div>
      )}
      <header className="fixed right-0 top-0 z-40 flex h-[44px] items-center justify-between border-b border-line bg-chrome/95 backdrop-blur" style={{ left: 0 }} id="topbar">
        <style>{`@media (min-width:1024px){#topbar{left:${W}px !important}}`}</style>
        <div className="flex h-full min-w-0 items-center overflow-hidden">
          <button className="px-3 lg:hidden" data-testid="open-drawer" onClick={() => setOpen(true)} aria-label="Open menu"><Menu size={18} /></button>
          <div className="flex items-center gap-1.5 pr-2 lg:hidden"><Logo size={20} /><span className="hidden whitespace-nowrap text-[13px] font-bold sm:inline">{APP_SHORT}</span></div>
          <Ticker />
        </div>
        <div className="flex shrink-0 items-center gap-2 pr-3">
          {user?.role === "admin" && state && (
            <Link to="/settings" data-testid="header-alert-mode" title="Alert mode (Settings → Signal alerts)"
              className={`hidden items-center gap-1 rounded-sm border px-1.5 py-px font-mono text-[10px] font-semibold md:inline-flex ${state.alert_mode_armed ? "border-teal-500/45 text-teal-300" : "border-amber-500/40 text-amber-300"}`}>
              <span className={`h-1.5 w-1.5 rounded-full ${state.alert_mode_armed ? "bg-teal-400" : "bg-amber-400"}`} />ALERTS {state.alert_mode_armed ? "ON" : "OFF"}
            </Link>
          )}
          <span title={streamOk ? "Live push connected: data updates the moment it changes" : "Live push not connected: pages refresh on a timer"}
            className={`hidden items-center gap-1 font-mono text-[10px] md:inline-flex ${streamOk ? "text-teal-300" : "text-slate-500"}`} data-testid="stream-status">
            <span className={`h-1.5 w-1.5 rounded-full ${streamOk ? "pulse-dot bg-teal-400" : "bg-slate-600"}`} />{streamOk ? "LIVE" : "POLL"}
          </span>
          <span className="hidden font-mono text-[10.5px] text-slate-500 lg:inline" data-testid="last-update">{ago(conn.lastOk)}</span>
          <span title={connTitle} className="hidden sm:inline"><Chip status={connTone} label={connStatus} testid="connection-status" /></span>
          <span title={`${connStatus}. ${connTitle || ""}`} aria-label={`Connection: ${connStatus}`} data-testid="connection-dot"
            className={`h-2.5 w-2.5 rounded-full sm:hidden ${connTone === "PASS" ? "bg-teal-400" : connTone === "WAIT" ? "bg-amber-400" : "bg-red-500"}`} />
          <span className="hidden sm:inline"><Clock /></span>
          <button data-testid="refresh-button" onClick={refresh} className="rounded-sm border border-line p-1 text-slate-400 transition-colors hover:border-line2 hover:text-slate-100" title="Refresh now">
            <RefreshCw size={13} className={conn.loading ? "animate-spin" : ""} />
          </button>
          <UserMenu user={user} logout={logout} onCustomize={() => setEditing(true)} />
        </div>
      </header>
      <main className="pt-[44px]" style={{ paddingLeft: 0 }} id="main">
        <style>{`@media (min-width:1024px){#main{padding-left:${W}px !important}}`}</style>
        {(!online || conn.ok === false) && (
          <div data-testid="offline-banner" className="flex items-center gap-2 border-b border-red-500/40 bg-red-950/70 px-4 py-1.5 text-[12px] text-red-200">
            <WifiOff size={14} /> {online ? "Server unreachable." : "You are offline."} Values on screen are the last received and are NOT live. Do not trade from them.
          </div>
        )}
        <div key={loc.pathname} className="page-enter mx-auto max-w-[1720px] p-3 sm:p-4"><SecurityBanner />{hiddenForUsers ? <Navigate to="/" replace /> : <Suspense fallback={<div className="grid min-h-[40vh] place-items-center font-mono text-[12px] text-slate-500">Loading…</div>}><Outlet /></Suspense>}</div>
        <SiteFooter />
      </main>
      {editing && <MenuEditor onClose={() => setEditing(false)} />}
    </div>
  );
}
