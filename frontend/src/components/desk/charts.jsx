import React, { useMemo, useRef, useState } from "react";
import { n, ist, sgn } from "@/lib/fmt";

// Chart tokens. Categorical slots are the validated dark steps (blue, orange, aqua) checked against
// the card surface #131c35. Up/down is polarity: teal / red, always paired with a sign or arrow.
export const VIZ = {
  surface: "#131c35", grid: "#1e2a4a", axis: "#64748b", text: "#e2e8f0", muted: "#94a3b8",
  up: "#2dd4bf", down: "#f87171", neutral: "#475569", brand: "#f59e0b",
  s1: "#3987e5", s2: "#d95926", s3: "#199e70",
};

// Shared hover: maps the pointer to the nearest data index inside a viewBox-based SVG.
function useHover(count, x0, x1, W) {
  const ref = useRef(null);
  const [i, setI] = useState(null);
  const onMove = (e) => {
    const r = ref.current?.getBoundingClientRect();
    if (!r || count < 1) return;
    const vx = ((e.clientX - r.left) / r.width) * W;
    const f = (vx - x0) / Math.max(1, x1 - x0);
    setI(Math.max(0, Math.min(count - 1, Math.round(f * (count - 1)))));
  };
  return { ref, i, onMove, onLeave: () => setI(null) };
}

function Tip({ x, W, children }) {
  const left = `${(x / W) * 100}%`;
  const flip = x > W * 0.62;
  return (
    <div className="pointer-events-none absolute top-1 z-10 rounded-md border border-[#2a3a63] bg-[#0b132b]/95 px-2.5 py-1.5 text-[11px] leading-snug text-slate-200 shadow-xl"
      style={{ left, transform: flip ? "translateX(calc(-100% - 10px))" : "translateX(10px)" }}>{children}</div>
  );
}

/* ------------------------------------------------------------------ progress ring */
export function Ring({ value, max = 1, size = 64, stroke = 6, color = VIZ.brand, label, sub, testid }) {
  const f = max ? Math.max(0, Math.min(1, (value ?? 0) / max)) : 0;
  const r = (size - stroke) / 2, c = 2 * Math.PI * r;
  return (
    <div className="flex items-center gap-3" data-testid={testid}>
      <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`} role="img" aria-label={`${label}: ${max === 1 ? Math.round(f * 100) + "%" : value + " of " + max}`}>
        <circle cx={size / 2} cy={size / 2} r={r} fill="none" stroke={VIZ.grid} strokeWidth={stroke} />
        <circle cx={size / 2} cy={size / 2} r={r} fill="none" stroke={color} strokeWidth={stroke} strokeLinecap="round"
          strokeDasharray={`${c * f} ${c}`} transform={`rotate(-90 ${size / 2} ${size / 2})`} style={{ transition: "stroke-dasharray .6s ease" }} />
        <text x="50%" y="52%" textAnchor="middle" dominantBaseline="middle" fill={VIZ.text} fontSize={size * 0.24} fontWeight="700" fontFamily="JetBrains Mono">
          {max === 1 ? `${Math.round(f * 100)}` : `${value ?? "—"}`}
        </text>
      </svg>
      <div className="min-w-0">
        <div className="text-[13px] font-semibold text-slate-100">{label}</div>
        {sub && <div className="text-[11.5px] text-slate-400">{sub}</div>}
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ sparkline with hover */
export function Sparkline({ bars, height = 64, testid = "sparkline" }) {
  const W = 320, H = height, P = 2;
  const h = useHover(bars?.length || 0, P, W - P, W);
  const g = useMemo(() => {
    if (!bars?.length) return null;
    const lo = Math.min(...bars.map((b) => b.l)), hi = Math.max(...bars.map((b) => b.h));
    const x = (i) => P + (i / Math.max(1, bars.length - 1)) * (W - 2 * P);
    const y = (v) => P + (hi - v) / (hi - lo || 1) * (H - 2 * P);
    const pts = bars.map((b, i) => `${x(i)},${y(b.c)}`).join(" ");
    const up = bars[bars.length - 1].c >= bars[0].o;
    return { x, y, pts, up, area: `${x(0)},${H} ${pts} ${x(bars.length - 1)},${H}` };
  }, [bars, H]);
  if (!g) return <div style={{ height }} className="grid place-items-center text-[11px] text-slate-500">loading</div>;
  const col = g.up ? VIZ.up : VIZ.down;
  const b = h.i != null ? bars[h.i] : null;
  return (
    <div className="relative" data-testid={testid}>
      <svg ref={h.ref} viewBox={`0 0 ${W} ${H}`} className="block h-auto w-full" preserveAspectRatio="none" onMouseMove={h.onMove} onMouseLeave={h.onLeave}>
        <defs>
          <linearGradient id={`${testid}-fill`} x1="0" y1="0" x2="0" y2="1">
            <stop offset="0" stopColor={col} stopOpacity="0.28" /><stop offset="1" stopColor={col} stopOpacity="0" />
          </linearGradient>
        </defs>
        <polygon points={g.area} fill={`url(#${testid}-fill)`} />
        <polyline points={g.pts} fill="none" stroke={col} strokeWidth="2" strokeLinejoin="round" vectorEffect="non-scaling-stroke" />
        {b && <><line x1={g.x(h.i)} x2={g.x(h.i)} y1={0} y2={H} stroke={VIZ.muted} strokeWidth="1" vectorEffect="non-scaling-stroke" strokeDasharray="2 3" />
          <circle cx={g.x(h.i)} cy={g.y(b.c)} r="3.5" fill={col} stroke={VIZ.surface} strokeWidth="2" vectorEffect="non-scaling-stroke" /></>}
      </svg>
      {b && <Tip x={g.x(h.i)} W={W}><div className="font-semibold">{n(b.c, 1)} USD</div><div className="text-slate-400">{ist(b.t)}</div></Tip>}
    </div>
  );
}

/* ------------------------------------------------------------------ five pillars: diverging bars */
export function PillarBars({ pillars, names, testid = "pillar-bars" }) {
  const rows = Object.entries(pillars || {}).map(([k, p]) => ({ k, name: names[k] || k, valid: p.valid, score: p.score, w: p.weight, c: p.valid && p.score != null ? p.score * p.weight : null }));
  const total = rows.reduce((a, r) => a + (r.c || 0), 0);
  const maxW = Math.max(...rows.map((r) => r.w || 0), 0.01);
  return (
    <div data-testid={testid}>
      <div className="mb-2 flex items-center justify-between text-[11px] text-slate-400"><span>bearish</span><span>weighted contribution</span><span>bullish</span></div>
      <div className="space-y-2">
        {rows.map((r) => {
          const frac = r.c == null ? 0 : Math.min(1, Math.abs(r.c) / maxW);
          const pos = (r.c || 0) >= 0;
          return (
            <div key={r.k} className="grid grid-cols-[96px_1fr_52px] items-center gap-2" title={r.valid ? `${r.name}: score ${sgn(r.score, 2)} × weight ${r.w}` : `${r.name}: unavailable`}>
              <span className={`truncate text-[12.5px] ${r.valid ? "text-slate-200" : "text-slate-500"}`}>{r.name}</span>
              <div className="relative h-4 rounded bg-[#0f172a]">
                <div className="absolute inset-y-0 left-1/2 w-px bg-slate-600" />
                {r.valid ? (r.c ? (
                  <div className="absolute inset-y-0.5 rounded-sm" style={{ background: pos ? VIZ.up : VIZ.down, width: `${frac * 50}%`, left: pos ? "calc(50% + 1px)" : `calc(${50 - frac * 50}% - 1px)`, transition: "width .5s ease, left .5s ease" }} />
                ) : <div className="absolute left-1/2 top-1/2 h-1.5 w-1.5 -translate-x-1/2 -translate-y-1/2 rounded-full bg-slate-400" />)
                  : <div className="absolute inset-0 rounded" style={{ backgroundImage: "repeating-linear-gradient(135deg, transparent 0 5px, rgba(100,116,139,.25) 5px 6px)" }} />}
              </div>
              <span className={`text-right font-mono text-[12px] ${!r.valid ? "text-slate-500" : r.c > 0 ? "text-teal-300" : r.c < 0 ? "text-red-300" : "text-slate-300"}`}>{r.valid ? `${r.c > 0 ? "▲" : r.c < 0 ? "▼" : "•"} ${Math.abs(r.c || 0).toFixed(2)}` : "n/a"}</span>
            </div>
          );
        })}
      </div>
      <div className="mt-3 flex items-center justify-between border-t border-[#1e2a4a] pt-2 text-[12px]">
        <span className="text-slate-400">Net tilt of available pillars</span>
        <span className={`font-mono font-semibold ${total > 0 ? "text-teal-300" : total < 0 ? "text-red-300" : "text-slate-300"}`}>{total > 0 ? "▲" : total < 0 ? "▼" : "•"} {sgn(total, 2)}</span>
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ multi-timeframe strip */
export function MtfStrip({ rows, testid = "mtf-strip" }) {
  return (
    <div className="grid grid-cols-5 gap-1.5" data-testid={testid}>
      {(rows || []).map((r) => {
        const t = r.trend;
        const tone = t === "UP" ? "border-teal-500/40 bg-teal-500/10 text-teal-200" : t === "DOWN" ? "border-red-500/40 bg-red-500/10 text-red-200" : "border-slate-600/50 bg-slate-700/20 text-slate-300";
        return (
          <div key={r.tf} className={`rounded-md border px-2 py-2 text-center ${tone}`} title={r.close ? `${r.tf}: close ${n(r.close, 0)}, ${r.above_vwap ? "above" : "below"} VWAP, volume ×${n(r.volume_ratio, 2)}` : `${r.tf}: unavailable`}>
            <div className="font-mono text-[11px] text-slate-400">{r.tf}</div>
            <div className="mt-0.5 text-[15px] font-bold leading-none">{t === "UP" ? "▲" : t === "DOWN" ? "▼" : "◆"}</div>
            <div className="mt-1 text-[10.5px]">{t === "UNAVAILABLE" ? "n/a" : t.toLowerCase()}</div>
          </div>
        );
      })}
    </div>
  );
}

/* ------------------------------------------------------------------ time-series line with crosshair */
export function LineChart({ series, height = 180, yFmt = (v) => n(v, 0), xFmt = ist, testid = "line-chart", unit = "" }) {
  // series: [{ name, color, points: [{t, v}] }] — all series share one y-axis (same unit).
  const W = 720, H = height, L = 8, R = 64, T = 10, B = 22;
  const pts0 = series?.[0]?.points || [];
  const h = useHover(pts0.length, L, W - R, W);
  const g = useMemo(() => {
    const all = (series || []).flatMap((s) => s.points.map((p) => p.v)).filter((v) => v != null && Number.isFinite(v));
    if (!all.length || pts0.length < 2) return null;
    let lo = Math.min(...all), hi = Math.max(...all);
    if (hi === lo) { hi += 1; lo -= 1; }
    const pad = (hi - lo) * 0.08; lo -= pad; hi += pad;
    const x = (i) => L + (i / (pts0.length - 1)) * (W - L - R);
    const y = (v) => T + (hi - v) / (hi - lo) * (H - T - B);
    return { x, y, lo, hi };
  }, [series, pts0.length, H]);
  if (!g) return <div style={{ height }} className="grid place-items-center rounded border border-dashed border-[#1e2a4a] text-[12px] text-slate-500">Collecting history — the chart appears after a few snapshots.</div>;
  return (
    <div className="relative" data-testid={testid}>
      <svg ref={h.ref} viewBox={`0 0 ${W} ${H}`} className="block h-auto w-full" onMouseMove={h.onMove} onMouseLeave={h.onLeave} role="img" aria-label={series.map((s) => s.name).join(", ")}>
        {[0, 0.5, 1].map((f) => { const v = g.lo + (g.hi - g.lo) * f; return <g key={f}><line x1={L} x2={W - R} y1={g.y(v)} y2={g.y(v)} stroke={VIZ.grid} /><text x={W - R + 6} y={g.y(v) + 3.5} fontSize="10.5" fill={VIZ.axis} fontFamily="JetBrains Mono">{yFmt(v)}</text></g>; })}
        <text x={L} y={H - 6} fontSize="10.5" fill={VIZ.axis} fontFamily="JetBrains Mono">{xFmt(pts0[0].t)}</text>
        <text x={W - R} y={H - 6} fontSize="10.5" fill={VIZ.axis} fontFamily="JetBrains Mono" textAnchor="end">{xFmt(pts0[pts0.length - 1].t)}</text>
        {series.map((s) => (
          <polyline key={s.name} fill="none" stroke={s.color} strokeWidth="2" strokeLinejoin="round"
            points={s.points.map((p, i) => (p.v == null ? null : `${g.x(i)},${g.y(p.v)}`)).filter(Boolean).join(" ")} />
        ))}
        {h.i != null && <>
          <line x1={g.x(h.i)} x2={g.x(h.i)} y1={T} y2={H - B} stroke={VIZ.muted} strokeDasharray="2 3" />
          {series.map((s) => s.points[h.i]?.v != null && <circle key={s.name} cx={g.x(h.i)} cy={g.y(s.points[h.i].v)} r="4" fill={s.color} stroke={VIZ.surface} strokeWidth="2" />)}
        </>}
      </svg>
      {h.i != null && (
        <Tip x={g.x(h.i)} W={W}>
          <div className="text-slate-400">{xFmt(pts0[h.i]?.t)}</div>
          {series.map((s) => <div key={s.name} className="flex items-center gap-1.5"><span className="h-2 w-2 rounded-full" style={{ background: s.color }} />{s.name}: <b>{s.points[h.i]?.v == null ? "—" : `${yFmt(s.points[h.i].v)}${unit}`}</b></div>)}
        </Tip>
      )}
      {series.length > 1 && <div className="mt-1 flex flex-wrap gap-3 text-[11.5px] text-slate-300">{series.map((s) => <span key={s.name} className="flex items-center gap-1.5"><span className="h-0.5 w-4 rounded" style={{ background: s.color }} />{s.name}</span>)}</div>}
    </div>
  );
}

/* ------------------------------------------------------------------ signed bars (funding etc.) */
export function SignedBars({ points, height = 120, fmt = (v) => sgn(v, 4), xFmt = ist, testid = "signed-bars", unit = "" }) {
  const W = 720, H = height, L = 8, R = 64, T = 8, B = 20;
  const h = useHover(points?.length || 0, L, W - R, W);
  if (!points?.length) return <div style={{ height }} className="grid place-items-center text-[12px] text-slate-500">No history yet.</div>;
  const m = Math.max(...points.map((p) => Math.abs(p.v || 0)), 1e-12);
  const mid = T + (H - T - B) / 2, half = (H - T - B) / 2;
  const bw = (W - L - R) / points.length;
  return (
    <div className="relative" data-testid={testid}>
      <svg ref={h.ref} viewBox={`0 0 ${W} ${H}`} className="block h-auto w-full" onMouseMove={h.onMove} onMouseLeave={h.onLeave}>
        <line x1={L} x2={W - R} y1={mid} y2={mid} stroke={VIZ.neutral} />
        <text x={W - R + 6} y={T + 8} fontSize="10.5" fill={VIZ.axis} fontFamily="JetBrains Mono">+{fmt(m).replace(/^\+/, "")}</text>
        <text x={W - R + 6} y={H - B} fontSize="10.5" fill={VIZ.axis} fontFamily="JetBrains Mono">-{fmt(m).replace(/^[+-]/, "")}</text>
        {points.map((p, i) => {
          const hgt = (Math.abs(p.v || 0) / m) * half, pos = (p.v || 0) >= 0;
          return <rect key={i} x={L + i * bw + 1} width={Math.max(1, bw - 2)} y={pos ? mid - hgt : mid} height={Math.max(1, hgt)} rx="2"
            fill={pos ? VIZ.up : VIZ.down} opacity={h.i == null || h.i === i ? 1 : 0.45} />;
        })}
      </svg>
      {h.i != null && <Tip x={L + h.i * bw} W={W}><div className="text-slate-400">{xFmt(points[h.i].t)}</div><b>{fmt(points[h.i].v)}{unit}</b></Tip>}
    </div>
  );
}

/* ------------------------------------------------------------------ outcome split (stacked bar) */
export function OutcomeBar({ counts, order, colors, testid = "outcome-bar" }) {
  const total = order.reduce((a, k) => a + (counts?.[k] || 0), 0);
  if (!total) return <div className="text-[12px] text-slate-500">No outcomes yet.</div>;
  return (
    <div data-testid={testid}>
      <div className="flex h-3 gap-[2px] overflow-hidden rounded">
        {order.filter((k) => counts?.[k]).map((k) => <div key={k} title={`${k}: ${counts[k]}`} style={{ width: `${(counts[k] / total) * 100}%`, background: colors[k] }} />)}
      </div>
      <div className="mt-1.5 flex flex-wrap gap-x-3 gap-y-1 text-[11.5px] text-slate-300">
        {order.filter((k) => counts?.[k]).map((k) => <span key={k} className="flex items-center gap-1.5"><span className="h-2 w-2 rounded-sm" style={{ background: colors[k] }} />{k.toLowerCase()} {counts[k]} ({Math.round((counts[k] / total) * 100)}%)</span>)}
      </div>
    </div>
  );
}
