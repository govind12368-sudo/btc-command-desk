import React, { useEffect, useRef } from "react";
import { createChart, CandlestickSeries, HistogramSeries, LineSeries, CrosshairMode } from "lightweight-charts";

const IST = 19800; // the chart shows India time
const UP = "#2dd4bf", DOWN = "#f87171";

/** Candles, volume, EMA 20/50/200 and the nearest support / resistance zones (TradingView Lightweight Charts). */
export default function IntelChart({ data, height = 380 }) {
  const box = useRef(null);
  const api = useRef(null);

  useEffect(() => {
    const chart = createChart(box.current, {
      height, autoSize: true,
      layout: { background: { color: "transparent" }, textColor: "#94a3b8", fontFamily: "JetBrains Mono, monospace", fontSize: 11 },
      grid: { vertLines: { color: "rgba(30,42,74,0.5)" }, horzLines: { color: "rgba(30,42,74,0.5)" } },
      rightPriceScale: { borderColor: "#1e2a4a" }, timeScale: { borderColor: "#1e2a4a", timeVisible: true, secondsVisible: false },
      crosshair: { mode: CrosshairMode.Normal },
    });
    const candles = chart.addSeries(CandlestickSeries, { upColor: UP, downColor: DOWN, borderUpColor: UP, borderDownColor: DOWN, wickUpColor: UP, wickDownColor: DOWN, priceFormat: { type: "price", precision: 1, minMove: 0.5 } });
    const volume = chart.addSeries(HistogramSeries, { priceFormat: { type: "volume" }, priceScaleId: "vol", lastValueVisible: false, priceLineVisible: false });
    chart.priceScale("vol").applyOptions({ scaleMargins: { top: 0.82, bottom: 0 } });
    const line = (color, title) => chart.addSeries(LineSeries, { color, lineWidth: 1, title, lastValueVisible: false, priceLineVisible: false, crosshairMarkerVisible: false });
    api.current = { chart, candles, volume, ema20: line("#fbbf24", "EMA20"), ema50: line("#60a5fa", "EMA50"), ema200: line("#c084fc", "EMA200"), lines: [], tf: null };
    return () => { chart.remove(); api.current = null; };
  }, [height]);

  useEffect(() => {
    const a = api.current;
    if (!a || !data?.bars?.length) return;
    const t = (b) => b.t + IST;
    a.candles.setData(data.bars.map((b) => ({ time: t(b), open: b.o, high: b.h, low: b.l, close: b.c })));
    a.volume.setData(data.bars.map((b) => ({ time: t(b), value: b.v, color: b.c >= b.o ? "rgba(45,212,191,0.35)" : "rgba(248,113,113,0.35)" })));
    for (const k of ["ema20", "ema50", "ema200"]) {
      const s = data.overlays?.[k] || [];
      a[k].setData(data.bars.map((b, i) => (s[i] == null ? null : { time: t(b), value: s[i] })).filter(Boolean));
    }
    a.lines.forEach((l) => a.candles.removePriceLine(l));
    a.lines = [];
    const add = (z, color, title) => a.lines.push(a.candles.createPriceLine({ price: z.price, color, lineWidth: 1, lineStyle: 2, axisLabelVisible: true, title }));
    (data.support || []).slice(0, 2).forEach((z, i) => add(z, "rgba(45,212,191,0.8)", `S${i + 1}`));
    (data.resistance || []).slice(0, 2).forEach((z, i) => add(z, "rgba(248,113,113,0.8)", `R${i + 1}`));
    if (a.tf !== data.timeframe) { a.chart.timeScale().fitContent(); a.tf = data.timeframe; }
  }, [data]);

  return <div ref={box} data-testid="intel-chart" style={{ height }} role="img" aria-label={`BTC ${data?.timeframe || ""} candle chart with EMA 20, 50, 200, volume and support and resistance lines`} />;
}
