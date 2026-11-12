import React from "react";
import { useDesk } from "@/context/DeskContext";
import { Card, Chip, KV, PageHead, Empty, Note } from "@/components/desk/ui";
import { PillarCard, DirectionPanel, PILLAR_NAMES } from "@/components/desk/Pillars";
import { n, ist, sgn } from "@/lib/fmt";
import { WorkbookPillarFields } from "@/components/desk/WorkbookFields";

const FACTOR_NAMES = { macro: "Macro / liquidity", etf: "ETF supply / inflows", funding_all: "All-exchange annualized funding", fear_greed: "Fear & Greed" };
const SERIES = { usd: "Broad USD (H.10 JRXWTFB, ≠ ICE DXY)", y2: "US 2-year Treasury (H.15)", brent: "Brent spot (EIA RBRTE)" };

export default function Pillars() {
  const { state: s, isAdmin } = useDesk();
  if (!s?.pillars) return <Empty>Waiting for first calculation…</Empty>;
  const ff = s.four_factor, sx = s.six_factor;
  return (
    <div className="space-y-4">
      <PageHead title="Five Pillars" sub="Live observation of Money Flow, Liquidity, Positioning (OKX BTC-USDT-SWAP), Macro (Fed/EIA) and Technical. Kept separate from the original four- and six-factor models." />
      <Card title="Live BTC direction" testid="pillars-direction"><DirectionPanel live={s.live_direction} testid="pillars-dir" /></Card>
      <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">{Object.entries(s.pillars).map(([k, p]) => <PillarCard key={k} k={k} p={p} />)}</div>
      <Card title="Macro inputs & publication-aware freshness" sub={`Mode: ${s.macro?.usd?.mode} · observation dates and publication dates kept separate`} testid="macro-table">
        <div className="overflow-x-auto scroll-thin"><table className="desk-table">
          <thead><tr><th>Series</th><th>Latest obs</th><th>Value</th><th>Prior</th><th>Release</th><th>Next publication</th><th>Obs age / cap</th><th>Status</th></tr></thead>
          <tbody>{Object.entries(s.macro || {}).map(([k, m]) => (
            <tr key={k}><td className="text-xs">{SERIES[k]}<div className="font-mono text-[10px] text-slate-500">{m.unit}</div></td>
              <td className="font-mono">{m.latest_date || "—"}</td><td className="font-mono">{m.latest_value ?? "—"}</td><td className="font-mono">{m.prior_value ?? "—"} <span className="text-slate-500 text-[10px]">{m.prior_date}</span></td>
              <td className="font-mono">{m.release_date || "—"}<div className="text-[10px] text-slate-500">{m.schedule}</div></td>
              <td className="font-mono">{m.next_publication || "—"} {m.next_publication_inferred && <span className="text-[10px] text-amber-400">(inferred)</span>}</td>
              <td className="font-mono">{m.observation_age_h}h / {m.cap_h ?? "—"}h</td>
              <td>{m.valid ? <Chip status="PASS" /> : <><Chip status="UNAVAILABLE" /><div className="text-[10.5px] text-red-300 mt-1 max-w-[260px]">{m.issues.join("; ")}</div></>}</td></tr>
          ))}</tbody></table></div>
        <div className="mt-2 grid sm:grid-cols-3 gap-x-6 font-mono text-[11px] text-slate-400">
          <span>USD d/d {sgn(s.macro?.usd?.daily_change_pct, 3)}% · 1m {sgn(s.macro?.usd?.month_change_pct, 2)}% (base {s.macro?.usd?.month_base_date})</span>
          <span>2Y d/d {sgn(s.macro?.y2?.daily_change_pp, 2)} pp · 5-obs {sgn(s.macro?.y2?.five_obs_change_bps, 0)} bps</span>
          <span>Brent ${s.macro?.brent?.latest_value} on {s.macro?.brent?.latest_date}</span>
        </div>
      </Card>
      <div className="grid gap-4 lg:grid-cols-2">
        <Card title="Original four-factor model" sub="Weights 0.25 / 0.25 / 0.30 / 0.20 · missing inputs stay unavailable (never neutral)" testid="four-factor"
          right={<Chip status={ff.complete ? (ff.score > 0 ? "BULLISH" : ff.score < 0 ? "BEARISH" : "NEUTRAL") : "UNAVAILABLE"} label={ff.complete ? `${ff.band} ${sgn(ff.score)}` : "INCOMPLETE"} testid="four-factor-status" />}>
          {Object.entries(ff.factors).map(([k, f]) => (
            <div key={k} className="py-2 border-b border-[#1e2a4a]/50">
              <div className="flex items-center justify-between"><span className="text-sm">{FACTOR_NAMES[k]} <span className="font-mono text-[10.5px] text-slate-500">w {f.weight}</span></span><Chip status={f.valid ? (f.score > 0 ? "BULLISH" : f.score < 0 ? "BEARISH" : "NEUTRAL") : "UNAVAILABLE"} label={f.valid ? sgn(f.score, 0) : "UNAVAILABLE"} /></div>
              <p className="mt-1 font-mono text-[11px] text-slate-400">{f.detail}</p>
            </div>
          ))}
          <p className="mt-2 text-[11px] text-slate-500">Thresholds (inclusive): USD 1m ≤ −1% / ≥ +1%; 2Y 5-obs ≤ −10 / ≥ +10 bps; Brent ≤ $70 / ≥ $85; ETF ≥ +$300m / ≤ −$300m; funding ≤ −5% / ≥ +15%; F&G ≤ 45 / ≥ 70. Bands ±0.2 directional, ±0.5 strong.</p>
        </Card>
        <Card title="Original six-factor model" sub="Money Flow 0.20 · Liquidity 0.15 · Positioning 0.15 · Macro 0.20 · Technical 0.20 · four-factor 0.10" testid="six-factor"
          right={<Chip status={sx.complete ? sx.direction : "UNAVAILABLE"} label={sx.complete ? `${sx.direction} ${sx.strength}` : "UNAVAILABLE"} testid="six-factor-status" />}>
          {sx.complete ? <KV k="Score" v={sgn(sx.score, 3)} /> : <Note tone="amber">Every contribution must be current. Missing: {sx.missing.join("; ")}. A partial observation is never used to complete this model or its validation log.</Note>}
          <div className="mt-2">{Object.entries(sx.contributions || {}).map(([k, v]) => <KV key={k} k={PILLAR_NAMES[k] || "Four-factor score"} v={sgn(v, 2)} />)}</div>
          <p className="mt-2 text-[11px] text-slate-500">Strength: |score| ≥ 0.70 very strong, ≥ 0.45 strong, ≥ 0.15 moderate, otherwise low.</p>
        </Card>
      </div>
      {isAdmin && <WorkbookPillarFields />}
      <Note>Model tilt is a weighted score on a −1…+1 scale, not a measured win probability. See Trade Validation for observed outcomes of logged direction changes.</Note>
    </div>
  );
}
