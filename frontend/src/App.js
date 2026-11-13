import React, { useEffect } from "react";
import { installAlertSound } from "@/lib/sound";
import { BrowserRouter, Routes, Route, Navigate, useLocation } from "react-router-dom";
import { Toaster } from "sonner";
import { DeskProvider, useDesk } from "@/context/DeskContext";
import Layout from "@/components/desk/Layout";
import Login from "@/pages/Login";
import Register from "@/pages/auth/Register";
import { Forgot, Reset, VerifyEmail } from "@/pages/auth/Recovery";
import Disclaimer from "@/pages/Disclaimer";
import Account from "@/pages/Account";
import AdminUsers from "@/pages/AdminUsers";
import Dashboard from "@/pages/Dashboard";
import Market from "@/pages/Market";
import StrategyPage from "@/pages/StrategyPage";
import Pillars from "@/pages/Pillars";
import Scanner from "@/pages/Scanner";
import News from "@/pages/News";
import Journal from "@/pages/Journal";
import Validation from "@/pages/Validation";
import SettingsPage from "@/pages/SettingsPage";
import Audit from "@/pages/Audit";
import Upgrades from "@/pages/Upgrades";
import PlanA from "@/pages/PlanA";
import AutoTrade from "@/pages/AutoTrade";

function Public() {
  const loc = useLocation();
  return (
    <Routes>
      <Route path="/login" element={<Login />} />
      <Route path="/register" element={<Register />} />
      <Route path="/forgot-password" element={<Forgot />} />
      <Route path="/reset-password" element={<Reset />} />
      <Route path="/verify-email" element={<VerifyEmail />} />
      <Route path="/disclaimer" element={<Disclaimer />} />
      <Route path="*" element={<Navigate to="/login" replace state={{ from: loc.pathname }} />} />
    </Routes>
  );
}

function Gate() {
  const { user, isAdmin } = useDesk();
  if (user === null) return <div className="grid min-h-screen place-items-center font-mono text-sm text-slate-500">Checking session…</div>;
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
        <Route path="/planner" element={<Navigate to="/scanner" replace />} />
        <Route path="/news" element={<News />} />
        <Route path="/journal" element={<Journal />} />
        <Route path="/plan-a" element={<PlanA />} />
        <Route path="/auto-trade" element={isAdmin ? <AutoTrade /> : <Navigate to="/" replace />} />
        <Route path="/validation" element={<Validation />} />
        <Route path="/settings" element={isAdmin ? <SettingsPage /> : <Navigate to="/" replace />} />
        <Route path="/audit" element={isAdmin ? <Audit /> : <Navigate to="/" replace />} />
        <Route path="/upgrades" element={isAdmin ? <Upgrades /> : <Navigate to="/" replace />} />
        <Route path="/account" element={isAdmin ? <Account /> : <Navigate to="/" replace />} />
        <Route path="/disclaimer" element={<Disclaimer />} />
        <Route path="/users" element={isAdmin ? <AdminUsers /> : <Navigate to="/" replace />} />
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
      <Toaster theme="dark" position="bottom-right" toastOptions={{ style: { background: "#131c35", border: "1px solid #1e2a4a", color: "#e2e8f0" } }} />
    </DeskProvider>
  );
}
