import React, { Suspense, lazy, useEffect } from "react";
import { installAlertSound } from "@/lib/sound";
import { BrowserRouter, Routes, Route, Navigate, useLocation } from "react-router-dom";
import { Toaster } from "sonner";
import { DeskProvider, useDesk } from "@/context/DeskContext";
import Layout from "@/components/desk/Layout";
import Login from "@/pages/Login";
import Disclaimer from "@/pages/Disclaimer";
import { Forgot, Reset, VerifyEmail } from "@/pages/auth/Recovery";
// Every page is loaded only when it is opened (smaller first download, faster start).
const Register = lazy(() => import("@/pages/auth/Register"));
const Account = lazy(() => import("@/pages/Account"));
const AdminUsers = lazy(() => import("@/pages/AdminUsers"));
const Dashboard = lazy(() => import("@/pages/Dashboard"));
const Market = lazy(() => import("@/pages/Market"));
const StrategyPage = lazy(() => import("@/pages/StrategyPage"));
const Pillars = lazy(() => import("@/pages/Pillars"));
const Scanner = lazy(() => import("@/pages/Scanner"));
const Intel = lazy(() => import("@/pages/Intel"));
const News = lazy(() => import("@/pages/News"));
const Journal = lazy(() => import("@/pages/Journal"));
const Validation = lazy(() => import("@/pages/Validation"));
const SettingsPage = lazy(() => import("@/pages/SettingsPage"));
const Audit = lazy(() => import("@/pages/Audit"));
const Upgrades = lazy(() => import("@/pages/Upgrades"));
const PlanA = lazy(() => import("@/pages/PlanA"));
const RegimeJournal = lazy(() => import("@/pages/RegimeJournal"));
const FlushShort = lazy(() => import("@/pages/FlushShort"));
const CandleRuns = lazy(() => import("@/pages/CandleRuns"));
const DeltaTop3 = lazy(() => import("@/pages/DeltaTop3"));
const EliteEdge = lazy(() => import("@/pages/EliteEdge"));
const ServerHealth = lazy(() => import("@/pages/ServerHealth"));
const OptionPicks = lazy(() => import("@/pages/OptionPicks"));
const FundManager = lazy(() => import("@/pages/FundManager"));
const StrategyObserver = lazy(() => import("@/pages/StrategyObserver"));
const AutoTrade = lazy(() => import("@/pages/AutoTrade"));
const DataHealth = lazy(() => import("@/pages/DataHealth"));

function Public() {
  const loc = useLocation();
  return (
    <Suspense fallback={<PageLoading />}>
    <Routes>
      <Route path="/login" element={<Login />} />
      <Route path="/register" element={<Register />} />
      <Route path="/forgot-password" element={<Forgot />} />
      <Route path="/reset-password" element={<Reset />} />
      <Route path="/verify-email" element={<VerifyEmail />} />
      <Route path="/disclaimer" element={<Disclaimer />} />
      <Route path="*" element={<Navigate to="/login" replace state={{ from: loc.pathname }} />} />
    </Routes>
    </Suspense>
  );
}

function PageLoading() {
  return <div className="grid min-h-[40vh] place-items-center font-mono text-[12px] text-slate-500">Loading…</div>;
}

function Gate() {
  const { user, isAdmin } = useDesk();
  if (user === null) return <div className="grid min-h-screen place-items-center font-mono text-[12px] text-slate-500">Checking session…</div>;
  if (!user) return <Public />;
  return (
    <Routes>
      <Route path="/verify-email" element={<VerifyEmail />} />
      <Route path="/reset-password" element={<Reset />} />
      <Route element={<Layout />}>
        <Route path="/" element={<Dashboard />} />
        <Route path="/market" element={<Market />} />
        <Route path="/scalping" element={<StrategyPage strat="scalp" key="scalp" />} />
        <Route path="/swing" element={<StrategyPage strat="swing" key="swing" />} />
        <Route path="/pillars" element={<Pillars />} />
        <Route path="/scanner" element={<Scanner />} />
        <Route path="/intel" element={<Intel />} />
        <Route path="/planner" element={<Navigate to="/scanner" replace />} />
        <Route path="/news" element={<News />} />
        <Route path="/journal" element={<Journal />} />
        <Route path="/plan-a" element={<PlanA />} />
        <Route path="/regime" element={<RegimeJournal />} />
        <Route path="/flush-short" element={<FlushShort />} />
        <Route path="/candle-runs" element={<CandleRuns />} />
        <Route path="/delta-top3" element={<DeltaTop3 />} />
        <Route path="/elite-4-edge" element={<EliteEdge />} />
        <Route path="/option-picks" element={<OptionPicks />} />
        <Route path="/fund-manager" element={<FundManager />} />
        <Route path="/observer" element={<StrategyObserver />} />
        <Route path="/auto-trade" element={isAdmin ? <AutoTrade /> : <Navigate to="/" replace />} />
        <Route path="/validation" element={<Validation />} />
        <Route path="/settings" element={isAdmin ? <SettingsPage /> : <Navigate to="/" replace />} />
        <Route path="/audit" element={isAdmin ? <Audit /> : <Navigate to="/" replace />} />
        <Route path="/upgrades" element={isAdmin ? <Upgrades /> : <Navigate to="/" replace />} />
        <Route path="/account" element={isAdmin ? <Account /> : <Navigate to="/" replace />} />
        <Route path="/disclaimer" element={<Disclaimer />} />
        <Route path="/users" element={isAdmin ? <AdminUsers /> : <Navigate to="/" replace />} />
        <Route path="/server" element={isAdmin ? <ServerHealth /> : <Navigate to="/" replace />} />
        <Route path="/data-health" element={isAdmin ? <DataHealth /> : <Navigate to="/" replace />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Route>
    </Routes>
  );
}

export default function App() {
  useEffect(() => {
    if ("serviceWorker" in navigator) navigator.serviceWorker.register("/sw.js").catch(() => {});
    installAlertSound();
  }, []);
  return (
    <DeskProvider>
      <BrowserRouter><Gate /></BrowserRouter>
      <Toaster theme="dark" position="bottom-right" toastOptions={{ style: { background: "#11151b", border: "1px solid #313945", color: "#e8ebef", borderRadius: 4, fontFamily: "IBM Plex Sans" } }} />
    </DeskProvider>
  );
}
