const IST = "Asia/Kolkata";

export const n = (v, d = 2) => (v === null || v === undefined || Number.isNaN(v) ? "—" : Number(v).toLocaleString("en-US", { minimumFractionDigits: d, maximumFractionDigits: d }));
export const usd = (v, d = 2) => (v === null || v === undefined || Number.isNaN(v) ? "—" : v < 0 ? `−$${n(-v, d)}` : `$${n(v, d)}`);
export const signedUsd = (v, d = 2) => (v === null || v === undefined || Number.isNaN(v) ? "—" : `${v > 0 ? "+" : v < 0 ? "−" : ""}$${n(Math.abs(v), d)}`);
export const pct = (v, d = 2) => (v === null || v === undefined ? "—" : `${(v * 100).toFixed(d)}%`);
export const sgn = (v, d = 2) => (v === null || v === undefined ? "—" : `${v > 0 ? "+" : ""}${Number(v).toFixed(d)}`);

const toDate = (v) => {
  if (v === null || v === undefined || v === "") return null;
  if (typeof v === "number") return new Date(v < 1e12 ? v * 1000 : v);
  return new Date(v);
};

export const ist = (v, withDate = true) => {
  const d = toDate(v);
  if (!d || Number.isNaN(d.getTime())) return "—";
  const o = { timeZone: IST, hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false };
  if (withDate) Object.assign(o, { day: "2-digit", month: "short" });
  return `${d.toLocaleString("en-GB", o)} IST`;
};

export const ago = (v) => {
  const d = toDate(v);
  if (!d) return "never";
  const s = Math.round((Date.now() - d.getTime()) / 1000);
  if (s < -5) return `in ${-s}s (future)`;
  if (s < 60) return `${Math.max(s, 0)}s ago`;
  if (s < 3600) return `${Math.round(s / 60)}m ago`;
  if (s < 86400) return `${Math.round(s / 3600)}h ago`;
  return `${Math.round(s / 86400)}d ago`;
};

export const tone = (v) => (v > 0 ? "text-teal-400" : v < 0 ? "text-red-400" : "text-slate-300");
