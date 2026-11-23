import axios from "axios";

// Same-origin by default ("/api" behind the Caddy proxy). Set REACT_APP_BACKEND_URL only if the API lives on another domain.
export const API = `${(process.env.REACT_APP_BACKEND_URL || "").replace(/\/$/, "")}/api`;
let token = null;
export const setToken = (t) => { token = t; };
export const getToken = () => token;

// The server tags every JSON answer with an ETag; we send it back and an unchanged answer comes back as an empty 304.
// The last copy of each answer is kept in memory only (never in the browser's disk cache).
const etags = new Map();
const MAX_ETAGS = 120;

export const api = axios.create({ baseURL: API, withCredentials: true, validateStatus: (s) => (s >= 200 && s < 300) || s === 304 });
api.interceptors.request.use((c) => {
  if (token) c.headers.Authorization = `Bearer ${token}`;
  if ((c.method || "get") === "get" && c.responseType !== "blob") {
    const hit = etags.get(c.url);
    if (hit) c.headers["If-None-Match"] = hit.etag;
  }
  return c;
});
let refreshing = null;
api.interceptors.response.use(
  (r) => {
    const cfg = r.config || {};
    if (r.status === 304) {
      const hit = etags.get(cfg.url);
      if (hit) return { ...r, status: 200, data: hit.data, notModified: true };
    } else if ((cfg.method || "get") === "get" && r.headers?.etag && cfg.responseType !== "blob") {
      if (etags.size >= MAX_ETAGS) etags.delete(etags.keys().next().value);
      etags.set(cfg.url, { etag: r.headers.etag, data: r.data });
    }
    return r;
  },
  async (err) => {
    const cfg = err.config || {};
    if (err.response?.status === 401 && !cfg._retry && !cfg.url?.includes("/auth/")) {
      cfg._retry = true;
      try {
        refreshing = refreshing || axios.post(`${API}/auth/refresh`, {}, { withCredentials: true });
        const { data } = await refreshing;
        refreshing = null;
        setToken(data.token);
        return api(cfg);
      } catch (e) {
        refreshing = null;
        window.dispatchEvent(new Event("desk-logout"));
      }
    }
    return Promise.reject(err);
  }
);

export function errText(e) {
  const d = e?.response?.data?.detail;
  if (d == null) return e?.response?.data && typeof e.response.data === "string" ? e.response.data : e?.message || "Request failed";
  if (typeof d === "string") return d;
  if (Array.isArray(d)) return d.map((x) => x?.msg || JSON.stringify(x)).join(" ");
  return d.msg || String(d);
}
