import axios from "axios";

// Same-origin by default ("/api" behind the Caddy proxy). Set REACT_APP_BACKEND_URL only if the API lives on another domain.
export const API = `${(process.env.REACT_APP_BACKEND_URL || "").replace(/\/$/, "")}/api`;
let token = null;
export const setToken = (t) => { token = t; };

export const api = axios.create({ baseURL: API, withCredentials: true });
api.interceptors.request.use((c) => {
  if (token) c.headers.Authorization = `Bearer ${token}`;
  return c;
});
let refreshing = null;
api.interceptors.response.use(
  (r) => r,
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
