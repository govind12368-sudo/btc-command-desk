import React, { createContext, useCallback, useContext, useEffect, useRef, useState } from "react";
import { api, setToken } from "@/lib/api";

const Ctx = createContext(null);
export const useDesk = () => useContext(Ctx);

export function DeskProvider({ children }) {
  const [user, setUser] = useState(null);
  const [state, setState] = useState(null);
  const [conn, setConn] = useState({ ok: null, lastOk: null, error: null, loading: false });
  const [online, setOnline] = useState(navigator.onLine);
  const timer = useRef(null);

  useEffect(() => {
    api.get("/auth/me").then((r) => setUser(r.data)).catch(() => setUser(false));
    const out = () => { setUser(false); setState(null); };
    const on = () => setOnline(true);
    const off = () => setOnline(false);
    window.addEventListener("desk-logout", out);
    window.addEventListener("online", on);
    window.addEventListener("offline", off);
    return () => { window.removeEventListener("desk-logout", out); window.removeEventListener("online", on); window.removeEventListener("offline", off); };
  }, []);

  const refresh = useCallback(async () => {
    setConn((c) => ({ ...c, loading: true }));
    try {
      const { data } = await api.get("/state");
      setState(data);
      setConn({ ok: true, lastOk: new Date().toISOString(), error: null, loading: false });
    } catch (e) {
      setConn((c) => ({ ...c, ok: false, error: e.message, loading: false }));
    }
  }, []);

  useEffect(() => {
    if (!user) return undefined;
    refresh();
    timer.current = setInterval(() => { if (!document.hidden) refresh(); }, 3000);
    return () => clearInterval(timer.current);
  }, [user, refresh]);

  const login = async (email, password, remember = true, otp = undefined) => {
    const { data } = await api.post("/auth/login", { email, password, remember, ...(otp ? { otp } : {}) });
    setToken(data.token);
    setUser(data);
  };
  const register = async (form) => {
    const { data } = await api.post("/auth/register", form);
    if (data.pending) return data; // admin approval required: no session yet
    setToken(data.token);
    setUser(data);
    return data;
  };
  const logout = async () => {
    await api.post("/auth/logout").catch(() => {});
    setToken(null);
    setUser(false);
    setState(null);
  };
  const isAdmin = user && user.role === "admin";
  const live = online && conn.ok;
  const refreshUser = () => api.get("/auth/me").then((r) => setUser(r.data)).catch(() => {});
  return <Ctx.Provider value={{ user, setUser, refreshUser, state, conn, online, live, refresh, login, register, logout, isAdmin }}>{children}</Ctx.Provider>;
}
