import React from "react";
import { Chip, Expand } from "./ui";
import { n, ist, sgn, pct } from "@/lib/fmt";

export const PILLAR_NAMES = { money_flow: "Money Flow", liquidity: "Liquidity", positioning: "Positioning", macro: "Macro", technical: "Technical" };

export function PillarCard({ k, p, compact }) {
  const sc = p.score;
  const st = !p.valid ? "UNAVAILABLE" : sc > 0 ? "BULLISH" : sc < 0 ? "BEARISH" : "NEUTRAL";
  return (
    <div className="desk-card p-3.5 fade-up" data-testid={`pillar-${k}`}>
      <div className="flex items-center justify-between gap-2">
        <span className="text-sm font-semibold">{PILLAR_NAMES[k]}</span>
        <Chip status={st} label={p.valid ? `${st} ${sgn(sc, 0)}` : "UNAVAILABLE"} />
      </div>
      <div className="mt-1 font-mono text-[10.5px] text-slate-500">weight {p.weight} · observed {p.observed_at?.length === 10 ? `${p.observed_at} (observation date)` : ist(p.observed_at)}</div>
      <p className={`mt-2 text-[11.5px] leading-relaxed ${p.valid ? "text-slate-300" : "text-slate-500"}`}>{compact ? String(p.detail).slice(0, 150) : p.detail}</p>
      {p.reason && <p className="mt-1 text-[11px] text-red-300/90">Why unavailable: {p.reason}</p>}
      {!compact && k === "positioning" && p.inputs?.funding_history && (
        <Expand label="Funding settlement history (OKX, fraction per 8h)">
          <div className="grid grid-cols-2 gap-1 font-mono text-[11px]">{p.inputs.funding_history.map((f) => <span key={f.time}>{ist(f.time)}: {sgn(f.realized_fraction * 100, 4)}%</span>)}</div>
          <div className="mt-2 font-mono text-[11px]">OI change 1h: {p.inputs.oi_change?.["1h_pct"] == null ? "UNAVAILABLE" : `${sgn(p.inputs.oi_change["1h_pct"])}%`} · 24h: {p.inputs.oi_change?.["24h_pct"] == null ? "UNAVAILABLE" : `${sgn(p.inputs.oi_change["24h_pct"])}%`} — {p.inputs.oi_change?.note || "collecting"}</div>
          {p.inputs.long_short_ratio?.[0] && <div className="font-mono text-[11px]">OKX long/short account ratio {n(p.inputs.long_short_ratio[0].ratio, 2)} ({ist(p.inputs.long_short_ratio[0].time)}) — supporting crowding context, not scored</div>}
        </Expand>
      )}
      {!compact && k === "liquidity" && p.inputs && (
        <Expand label="Book detail"><div className="font-mono text-[11px]">bid notional {n(p.inputs.bid_notional, 0)} USDT · ask {n(p.inputs.ask_notional, 0)} USDT · bid share {pct(p.inputs.bid_share, 1)} · sweep coverage {pct(p.inputs.sweep_cov_bid, 0)} / {pct(p.inputs.sweep_cov_ask, 0)} (needs ≥ 99%)</div></Expand>
      )}
    </div>
  );
}

export function DirectionPanel({ live, testid = "direction-panel" }) {
  if (!live) return null;
  const quality = live.data_quality;
  const paused = live.status === "PAUSED" || quality?.valid === false;
  const displayedDirection = paused ? "PAUSED" : live.direction || "WAIT";
  const pending = live.status === "CHANGE PENDING";
  const partial = quality?.partial ?? live.partial;
  const provenance = live.provenance || {};
  const qualityLabel = quality?.valid === false ? "REQUIRED DATA INVALID" : quality?.valid === true ? "REQUIRED DATA VALID" : "DATA NOT VALIDATED";
  const historical = live.last_confirmed_direction || live.confirmed_direction;
  return (
    <div data-testid={testid}>
      <div className="flex flex-wrap items-end gap-3">
        <span data-testid={`${testid}-direction`} className={`font-mono text-4xl sm:text-5xl font-extrabold tracking-tight ${displayedDirection === "LONG" ? "text-teal-400" : displayedDirection === "SHORT" ? "text-red-400" : "text-amber-300"}`}>{displayedDirection}</span>
        <Chip status={live.status || "WAIT"} label={live.status || "WAITING FOR CONFIRMATION"} testid={`${testid}-status`} />
      </div>
      <p className="mt-2 text-xs text-slate-300" data-testid={`${testid}-reason`}>{live.reason || "Waiting for a confirmed assessment."}</p>
      <div className="mt-3 flex flex-wrap items-center gap-2">
        <Chip status={quality?.valid === true ? "PASS" : "BLOCKED"} label={qualityLabel} testid={`${testid}-data-quality`} />
        {partial && <Chip status="WAIT" label="PARTIAL COVERAGE" testid={`${testid}-partial`} />}
        <span className="font-mono text-[10.5px] text-slate-500">current coverage {quality?.current_coverage ?? live.coverage ?? 0}/5</span>
      </div>
      <div className="mt-3 grid grid-cols-2 gap-x-4 gap-y-1 font-mono text-[11px]">
        <div className="text-slate-500">Candidate <span className="text-slate-200">{live.candidate_direction || "—"}</span></div>
        <div className="text-slate-500">Closed-candle confirmations <span className="text-slate-200">{live.pending_count ?? 0}/{live.confirmation_required ?? 2}</span></div>
        <div className="col-span-2 text-slate-500">{live.decision_paused || paused ? "Last confirmed direction (historical)" : "Retained confirmed direction"} <span className="text-slate-200">{historical || "none yet"}</span>{historical && live.confirmed_since ? ` · since ${ist(live.confirmed_since)}` : ""}</div>
      </div>
      {pending && <p className="mt-2 text-[11px] text-amber-300">The candidate change is unconfirmed. The displayed bias retains {live.confirmed_direction}; the score below describes the pending assessment.</p>}
      {paused && historical && <p className="mt-2 text-[11px] text-amber-300">The previous {historical} confirmation is historical. Current direction is paused until valid data and fresh candle confirmations return.</p>}
      {quality?.issues?.length > 0 && <ul className="mt-2 space-y-1 text-[11px] text-amber-300" data-testid={`${testid}-issues`}>{quality.issues.map((issue, i) => <li key={i}>• {issue}</li>)}</ul>}
      <div className="mt-3 font-mono text-xs text-slate-400">Frozen assessment score: <span className="text-slate-200">{live.score == null ? "unavailable" : sgn(live.score, 3)}</span>{live.strength ? ` · assessment strength ${live.strength}` : ""}</div>
      <div className="mt-3 h-2 w-full rounded bg-[#0f172a] relative overflow-hidden">
        <div className="absolute top-0 bottom-0 left-1/2 w-px bg-slate-600" />
        {live.score != null && <div className={`absolute top-0 bottom-0 ${live.score >= 0 ? "left-1/2 bg-teal-500" : "right-1/2 bg-red-500"}`} style={{ width: `${Math.min(50, Math.abs(live.score) * 50)}%` }} />}
      </div>
      <div className="mt-3 flex flex-wrap gap-1.5">
        {Object.entries(live.active_shares || {}).map(([k, v]) => <span key={k} className="rounded border border-[#1e2a4a] px-2 py-0.5 font-mono text-[10.5px] text-slate-300">{PILLAR_NAMES[k]} {pct(v, 0)} · {sgn(live.normalized_scores?.[k], 1)}</span>)}
        {(live.excluded || []).map((k) => <span key={k} className="rounded border border-gray-700/60 px-2 py-0.5 font-mono text-[10.5px] text-gray-500 line-through">{PILLAR_NAMES[k]}</span>)}
      </div>
      <p className="mt-2 text-[11px] text-slate-500">The score measures model tilt on a −1…+1 scale. Strength is not a measured win probability.</p>
      <p className="mt-2 text-[11px] text-slate-400">Inputs are frozen for each assessed completed 15-minute candle. A new direction needs {live.confirmation_required ?? 2} consecutive, distinct completed candles; refreshing the page adds no confirmations.</p>
      <div className="mt-2 font-mono text-[10.5px] text-slate-500">Candle close {ist(live.candle_close)} · assessed {ist(live.assessed_at)} · frozen coverage {live.coverage ?? 0}/5</div>
      <Expand label="Assessment sources & confirmation policy" testid={`${testid}-provenance`}>
        <div className="space-y-1 text-[11px] text-slate-400">
          <p>{live.observation_note || "Trade, order-book, funding and macro inputs are sampled when the assessment is made; they are not reconstructed historical candle-close values."}</p>
          <p className="font-mono">Model {live.model_version || "—"} · saved state {live.state_persisted === true ? "verified" : "unavailable"}</p>
          <p className="font-mono break-all">Snapshot {live.snapshot_id || "—"}</p>
          <p className="font-mono break-all">Input hash {live.input_hash || "—"}</p>
          <p className="font-mono">Source {provenance.source || "—"} · instrument {provenance.instrument || "—"} · retrieved {ist(provenance.retrieved_at)}</p>
          {provenance.url?.startsWith("http") && <a className="block text-amber-300 break-all" href={provenance.url} target="_blank" rel="noreferrer">{provenance.url}</a>}
          <p className="font-mono">Required pillars: {(quality?.required_pillars || []).map((k) => PILLAR_NAMES[k] || k).join(", ") || "—"} · quality checked {ist(quality?.checked_at)}</p>
          {Object.entries(provenance.sample_times || {}).map(([k, value]) => <p key={k} className="font-mono">{PILLAR_NAMES[k] || k} observation {String(value || "").length === 10 ? `${value} (observation date)` : ist(value)}</p>)}
          <p className="font-mono">Assessment weights are renormalized over available pillars; macro score is divided by 2.</p>
          {live.policy && <p className="font-mono">Entry threshold ±{live.policy.entry_threshold} · retention threshold ±{live.policy.exit_threshold} · {live.policy.consecutive_closed_candles} consecutive closed {live.policy.timeframe} candles</p>}
        </div>
      </Expand>
    </div>
  );
}
