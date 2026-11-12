import React, { useEffect, useMemo, useRef, useState } from "react";
import { api } from "@/lib/api";
import { ist, n, sgn } from "@/lib/fmt";
import { VIZ } from "./charts";

const W = 960, PH = 300, VH = 64, GAP = 10, RIGHT = 70, TOP = 8;
const H = TOP + PH + GAP + VH + 18;
const TF_SEC = { "5m": 300, "15m": 900, "1h": 3600, "4h": 14400, "1d": 86400 };

function ema(vals, p) {
  const k = 2 / (p + 1); const out = []; let e = null;
  vals.forEach((v, i) => { if (i === p - 1) e = vals.slice(0, p).reduce((a, b) => a + b, 0) / p; else if (i >= p) e = v * k + e * (1 - k); out.push(e); });
  return out;
}

export function CandleChart({ tf: tf0 = "5m", levels, testid = "candle-chart" }) {
  const [tf, setTf] = useState(tf0);
  const [d, setD] = useState(null);
  const [err, setErr] = useState(null);
  const [hi, setHi] = useState(null);
  const svg = useRef(null);
  useEffect(() => {
    let live = true;
    const load = () => api.get(`/candles?tf=${tf}`).then((r) => live && (setD(r.data), setErr(null))).catch((e) => live && setErr(e.message));
    load();
    const i = setInterval(load, 15000);
    return () => { live = false; clearInterval(i); };
  }, [tf]);
  const g = useMemo(() => {
    if (!d?.bars?.length) return null;
    const bars = d.bars.slice(-96);
    const all = d.bars.map((b) => b.c);
    const e20 = ema(all, 20).slice(-96), e50 = ema(all, 50).slice(-96);
    const lv = levels ? [levels.entry, levels.stop, levels.target].filter(Boolean) : [];
    const ev = [...e20, ...e50].filter((v) => v != null);
    let top = Math.max(...bars.map((b) => b.h), ...lv, ...ev), bot = Math.min(...bars.map((b) => b.l), ...lv, ...ev);
    const pad = (top - bot) * 0.04; top += pad; bot -= pad;
    const plotW = W - RIGHT;
    const bw = plotW / bars.length;
    const x = (i) => i * bw + bw / 2;
    const y = (v) => TOP + (top - v) / (top - bot || 1) * PH;
    const vmax = Math.max(...bars.map((b) => b.v || 0), 1);
    const vy = (v) => TOP + PH + GAP + VH - (v / vmax) * VH;
    return { bars, e20, e50, x, y, vy, bw, top, bot, plotW };
  }, [d, levels]);

  const onMove = (e) => {
    if (!g) return;
    const r = svg.current.getBoundingClientRect();
    const vx = ((e.clientX - r.left) / r.width) * W;
    if (vx > g.plotW) return setHi(null);
    setHi(Math.max(0, Math.min(g.bars.length - 1, Math.floor(vx / g.bw))));
  };
  const last = g?.bars[g.bars.length - 1];
  const hb = hi != null && g ? g.bars[hi] : null;
  const show = hb || last;
  const chg = show ? show.c - show.o : 0;
  return (
    <div data-testid={testid}>
      <div className="mb-2 flex flex-wrap items-center gap-1.5">
        <div className="flex rounded-md border border-[#1e2a4a] p-0.5" role="tablist" aria-label="Candle timeframe">
          {Object.keys(TF_SEC).map((t) => (
            <button key={t} role="tab" aria-selected={tf === t} data-testid={`${testid}-tf-${t}`} onClick={() => setTf(t)}
              className={`rounded px-2.5 py-1 font-mono text-[11.5px] transition-colors focus-visible:outline focus-visible:outline-2 focus-visible:outline-amber-400 ${tf === t ? "bg-amber-500/15 text-amber-200" : "text-slate-400 hover:text-slate-100"}`}>{t}</button>
          ))}
        </div>
        {show && (
          <div className="ml-1 flex flex-wrap items-baseline gap-x-3 gap-y-0.5 font-mono text-[11.5px]" data-testid={`${testid}-ohlc`}>
            <span className="text-slate-400">{ist(show.t)}</span>
            <span>O <b className="text-slate-100">{n(show.o, 1)}</b></span>
            <span>H <b className="text-slate-100">{n(show.h, 1)}</b></span>
            <span>L <b className="text-slate-100">{n(show.l, 1)}</b></span>
            <span>C <b className="text-slate-100">{n(show.c, 1)}</b></span>
            <span className={chg >= 0 ? "text-teal-300" : "text-red-300"}>{chg >= 0 ? "▲" : "▼"} {sgn((chg / show.o) * 100, 2)}%</span>
            <span className="text-slate-400">vol {n(show.v, 0)}</span>
          </div>
        )}
      </div>
      {err && <div className="text-xs text-red-300">Chart unavailable: {err}</div>}
      {!g ? <div className="grid h-[260px] place-items-center text-xs text-slate-500">Loading completed candles…</div> : (
        <svg ref={svg} viewBox={`0 0 ${W} ${H}`} className="block h-auto w-full touch-none" onMouseMove={onMove} onMouseLeave={() => setHi(null)}
          role="img" aria-label={`BTCUSD ${tf} candles, last close ${n(last.c, 1)}`}>
          {(() => { const tags = [last.c, ...(levels ? [levels.entry, levels.stop, levels.target] : [])].filter(Boolean).map((v) => g.y(v));
            return [0, 0.25, 0.5, 0.75, 1].map((f) => { const v = g.bot + (g.top - g.bot) * f; const clash = tags.some((t) => Math.abs(t - g.y(v)) < 14);
              return (<g key={f}><line x1={0} x2={g.plotW} y1={g.y(v)} y2={g.y(v)} stroke={VIZ.grid} />{!clash && <text x={g.plotW + 6} y={g.y(v) + 3.5} fontSize="10.5" fill={VIZ.axis} fontFamily="JetBrains Mono">{n(v, 0)}</text>}</g>); }); })()}
          <line x1={0} x2={g.plotW} y1={TOP + PH + GAP + VH} y2={TOP + PH + GAP + VH} stroke={VIZ.grid} />
          <text x={g.plotW + 6} y={TOP + PH + GAP + 10} fontSize="10" fill={VIZ.axis} fontFamily="JetBrains Mono">volume</text>
          {g.bars.map((b, i) => {
            const up = b.c >= b.o, c = up ? VIZ.up : VIZ.down, x = g.x(i), dim = hi != null && hi !== i ? 0.55 : 1;
            return (
              <g key={b.t} opacity={dim}>
                <line x1={x} x2={x} y1={g.y(b.h)} y2={g.y(b.l)} stroke={c} strokeWidth="1.2" />
                <rect x={x - g.bw * 0.34} width={Math.max(1.5, g.bw * 0.68)} y={g.y(Math.max(b.o, b.c))} height={Math.max(1.2, Math.abs(g.y(b.o) - g.y(b.c)))} fill={c} rx="1" />
                <rect x={x - g.bw * 0.34} width={Math.max(1.5, g.bw * 0.68)} y={g.vy(b.v || 0)} height={Math.max(0.5, TOP + PH + GAP + VH - g.vy(b.v || 0))} fill={c} opacity="0.45" rx="1" />
              </g>
            );
          })}
          {[[g.e20, VIZ.s1], [g.e50, VIZ.s2]].map(([arr, col], k) => (
            <polyline key={k} fill="none" stroke={col} strokeWidth="2" strokeLinejoin="round" points={arr.map((v, i) => (v ? `${g.x(i)},${g.y(v)}` : null)).filter(Boolean).join(" ")} />
          ))}
          {levels && [["entry", "#e2e8f0"], ["stop", VIZ.down], ["target", VIZ.up]].map(([k, col]) => levels[k] && !(k === "entry" && Math.abs(g.y(levels[k]) - g.y(last.c)) < 16) ? (
            <g key={k}>
              <line x1={0} x2={g.plotW} y1={g.y(levels[k])} y2={g.y(levels[k])} stroke={col} strokeDasharray="6 4" strokeWidth="1.3" />
              <rect x={g.plotW + 2} y={g.y(levels[k]) - 8} width={RIGHT - 4} height="16" rx="3" fill={VIZ.surface} stroke={col} />
              <text x={g.plotW + 6} y={g.y(levels[k]) + 3.5} fontSize="10" fill="#e2e8f0" fontFamily="JetBrains Mono">{k[0].toUpperCase()} {n(levels[k], 0)}</text>
            </g>
          ) : null)}
          {(() => { const up = last.c >= last.o; return (
            <g>
              <line x1={0} x2={g.plotW} y1={g.y(last.c)} y2={g.y(last.c)} stroke={up ? VIZ.up : VIZ.down} strokeOpacity="0.5" strokeDasharray="1 3" />
              <rect x={g.plotW + 2} y={g.y(last.c) - 9} width={RIGHT - 4} height="18" rx="3" fill={up ? VIZ.up : VIZ.down} />
              <text x={g.plotW + 6} y={g.y(last.c) + 4} fontSize="11" fontWeight="700" fill="#0b132b" fontFamily="JetBrains Mono">{n(last.c, 0)}</text>
            </g>); })()}
          {hi != null && <line x1={g.x(hi)} x2={g.x(hi)} y1={TOP} y2={TOP + PH + GAP + VH} stroke={VIZ.muted} strokeDasharray="2 3" />}
        </svg>
      )}
      <div className="mt-1.5 flex flex-wrap items-center gap-x-4 gap-y-1 text-[11.5px] text-slate-300">
        <span className="flex items-center gap-1.5"><span className="h-0.5 w-4 rounded" style={{ background: VIZ.s1 }} />EMA 20</span>
        <span className="flex items-center gap-1.5"><span className="h-0.5 w-4 rounded" style={{ background: VIZ.s2 }} />EMA 50</span>
        {levels && <span className="text-slate-400">dashed lines: entry (E), stop (S), target (T)</span>}
        <span className="ml-auto font-mono text-[10.5px] text-slate-500">Delta BTCUSD · completed candles only · retrieved {ist(d?.retrieved)}</span>
      </div>
    </div>
  );
}
