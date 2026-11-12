import React, { useEffect, useMemo, useState } from "react";
import { api } from "@/lib/api";
import { ist, n } from "@/lib/fmt";

const W = 900, H = 300, PAD = 48;

function ema(vals, p) {
  const k = 2 / (p + 1); const out = []; let e = null;
  vals.forEach((v, i) => { if (i === p - 1) e = vals.slice(0, p).reduce((a, b) => a + b, 0) / p; else if (i >= p) e = v * k + e * (1 - k); out.push(e); });
  return out;
}

export function CandleChart({ tf: tf0 = "5m", levels, testid = "candle-chart" }) {
  const [tf, setTf] = useState(tf0);
  const [d, setD] = useState(null);
  const [err, setErr] = useState(null);
  useEffect(() => {
    let live = true;
    const load = () => api.get(`/candles?tf=${tf}`).then((r) => live && (setD(r.data), setErr(null))).catch((e) => live && setErr(e.message));
    load();
    const i = setInterval(load, 15000);
    return () => { live = false; clearInterval(i); };
  }, [tf]);
  const g = useMemo(() => {
    if (!d?.bars?.length) return null;
    const bars = d.bars.slice(-90);
    const all = d.bars.map((b) => b.c);
    const e20 = ema(all, 20).slice(-90), e50 = ema(all, 50).slice(-90);
    const lv = levels ? [levels.entry, levels.stop, levels.target].filter(Boolean) : [];
    const hi = Math.max(...bars.map((b) => b.h), ...lv), lo = Math.min(...bars.map((b) => b.l), ...lv);
    const y = (v) => PAD / 2 + (hi - v) / (hi - lo || 1) * (H - PAD);
    const bw = (W - PAD) / bars.length;
    return { bars, e20, e50, y, bw, hi, lo };
  }, [d, levels]);
  return (
    <div data-testid={testid}>
      <div className="mb-2 flex flex-wrap items-center gap-1">
        {["5m", "15m", "1h", "4h", "1d"].map((t) => (
          <button key={t} data-testid={`${testid}-tf-${t}`} onClick={() => setTf(t)} className={`rounded px-2 py-0.5 font-mono text-[11px] border transition-colors ${tf === t ? "border-amber-500/60 text-amber-300 bg-amber-500/10" : "border-[#1e2a4a] text-slate-400 hover:text-slate-100"}`}>{t}</button>
        ))}
        <span className="ml-auto font-mono text-[10.5px] text-slate-500">Delta BTCUSD · completed candles only · retrieved {ist(d?.retrieved)}</span>
      </div>
      {err && <div className="text-xs text-red-300">Chart unavailable: {err}</div>}
      {!g ? <div className="h-[220px] grid place-items-center text-xs text-slate-500">Loading completed candles…</div> : (
        <svg viewBox={`0 0 ${W} ${H}`} className="w-full h-auto">
          {[0, 0.25, 0.5, 0.75, 1].map((f) => { const v = g.lo + (g.hi - g.lo) * f; return (<g key={f}><line x1={0} x2={W - PAD} y1={g.y(v)} y2={g.y(v)} stroke="#1e2a4a" strokeDasharray="2 4" /><text x={W - PAD + 4} y={g.y(v) + 3} fontSize="10" fill="#64748b" fontFamily="JetBrains Mono">{n(v, 0)}</text></g>); })}
          {g.bars.map((b, i) => {
            const x = i * g.bw + g.bw / 2, up = b.c >= b.o, c = up ? "#14b8a6" : "#ef4444";
            return (<g key={b.t}><line x1={x} x2={x} y1={g.y(b.h)} y2={g.y(b.l)} stroke={c} /><rect x={x - g.bw * 0.32} width={g.bw * 0.64} y={g.y(Math.max(b.o, b.c))} height={Math.max(1, Math.abs(g.y(b.o) - g.y(b.c)))} fill={c} opacity={0.85} /></g>);
          })}
          {[[g.e20, "#f59e0b"], [g.e50, "#818cf8"]].map(([arr, col], k) => (
            <polyline key={k} fill="none" stroke={col} strokeWidth="1.3" points={arr.map((v, i) => (v ? `${i * g.bw + g.bw / 2},${g.y(v)}` : null)).filter(Boolean).join(" ")} />
          ))}
          {levels && [["entry", "#f8fafc"], ["stop", "#ef4444"], ["target", "#14b8a6"]].map(([k, col]) => levels[k] ? (
            <g key={k}><line x1={0} x2={W - PAD} y1={g.y(levels[k])} y2={g.y(levels[k])} stroke={col} strokeDasharray="6 3" /><text x={4} y={g.y(levels[k]) - 3} fontSize="10" fill={col} fontFamily="JetBrains Mono">{k} {n(levels[k], 1)}</text></g>
          ) : null)}
        </svg>
      )}
      <div className="mt-1 flex gap-4 font-mono text-[10.5px] text-slate-500"><span className="text-amber-400">— EMA20</span><span className="text-indigo-400">— EMA50</span><span>last completed close {ist(g && (g.bars[g.bars.length - 1].t + ({ "5m": 300, "15m": 900, "1h": 3600, "4h": 14400, "1d": 86400 }[tf])))}</span></div>
    </div>
  );
}
