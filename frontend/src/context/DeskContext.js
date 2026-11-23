import React, { createContext, useCallback, useContext, useEffect, useRef, useState } from "react";
import { API, api, setToken } from "@/lib/api";
import { live as liveStore, onTopic } from "@/lib/live";

const Ctx = createContext(null);
export const useDesk = () => useContext(Ctx);

const wsUrl = () => {
  const base = API.startsWith("http") ? API : `${window.location.origin}${API}`;
  return base.replace(/^http/, "ws") + "/ws";
};

export function DeskProvider({ children }) {
  const [user, setUser] = useState(null);
  const [state, setState] = useState(null);
  const [conn, setConn] = useState({ ok: null, lastOk: null, error: null, loading: false });
  const [online, setOnline] = useState(navigator.onLine);
  const [streamOk, setStreamOkState] = useState(false);   // live push socket up (the pulse itself lives in lib/live, outside React state)
  const setStreamOk = useCallback((v) => { liveStore.setOk(v); setStreamOkState(v); }, []);
  const [ui, setUi] = useState({ hidden_global: [], hidden_user: [], locked: ["/"] });
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

  // header/state: every 3 s without the live stream, every 12 s with it (plus at once when the server says it changed)
  useEffect(() => {
    if (!user) return undefined;
    refresh();
    timer.current = setInterval(() => { if (!document.hidden) refresh(); }, streamOk ? 12000 : 3000);
    return () => clearInterval(timer.current);
  }, [user, refresh, streamOk]);

  useEffect(() => (user ? onTopic("state", () => { if (!document.hidden) refresh(); }) : undefined), [user, refresh]);

  // live push socket with reconnect; a 4401 close means the access cookie expired -> refresh the session, then reconnect
  useEffect(() => {
    if (!user) return undefined;
    let ws = null, stop = false, wait = 1500, retryT = null;
    const connect = () => {
      if (stop) return;
      try { ws = new WebSocket(wsUrl()); } catch (e) { retryT = setTimeout(connect, wait); return; }
      ws.onopen = () => { wait = 1500; };
      ws.onmessage = (ev) => {
        try {
          const m = JSON.parse(ev.data);
          if (m.type === "pulse" || m.type === "hello") { liveStore.set({ price: m.price, v: m.v, t: m.t, clients: m.clients }); setStreamOk(true); }
        } catch (e) { /* ignore */ }
      };
      ws.onclose = async (ev) => {
        setStreamOk(false);
        if (stop) return;
        if (ev.code === 4401) { try { await api.get("/auth/me"); } catch (e) { /* the 401 handler signs out */ } }
        retryT = setTimeout(connect, wait);
        wait = Math.min(30000, wait * 2);
      };
      ws.onerror = () => { try { ws.close(); } catch (e) { /* */ } };
    };
    connect();
    const vis = () => { if (!document.hidden && ws && ws.readyState > 1) { clearTimeout(retryT); wait = 1500; connect(); } };
    document.addEventListener("visibilitychange", vis);
    return () => { stop = true; clearTimeout(retryT); document.removeEventListener("visibilitychange", vis); try { ws && ws.close(); } catch (e) { /* */ } setStreamOk(false); };
  }, [user, setStreamOk]);

  // menu hide / show
  const loadUi = useCallback(() => api.get("/ui/config").then((r) => setUi(r.data)).catch(() => {}), []);
  useEffect(() => { if (user) loadUi(); }, [user, loadUi]);
  const setHiddenUser = async (pages) => { const { data } = await api.put("/ui/hidden", { pages }); setUi(data); };
  const setHiddenGlobal = async (pages) => { const { data } = await api.put("/ui/hidden-global", { pages }); setUi(data); };

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
  return (
    <Ctx.Provider value={{ user, setUser, refreshUser, state, conn, online, live, refresh, login, register, logout, isAdmin, streamOk, ui, setHiddenUser, setHiddenGlobal }}>
      {children}
    </Ctx.Provider>
  );
}
