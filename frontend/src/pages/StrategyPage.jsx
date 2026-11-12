import React from "react";
import { useDesk } from "@/context/DeskContext";
import { useFetch } from "@/hooks/useFetch";
import { api, errText } from "@/lib/api";
import { toast } from "sonner";
import { Card, Chip, KV, PageHead, Empty, Note } from "@/components/desk/ui";
import { ChecksList, Levels, FrameTable } from "@/components/desk/Strategy";
import { CandleChart } from "@/components/desk/CandleChart";
import { n, ist } from "@/lib/fmt";

function PlanPanel({ isAdmin }) {
  const { data, reload } = useFetch("/plans", 10000);
  const cancel = async (id) => {
    try { await api.post(`/plans/${id}/cancel`); toast.success("Plan cancelled and archived"); reload(); } catch (e) { toast.error(errText(e)); }
  };
  return (
    <>
      <Card title="Saved Swing plan" sub="Immutable snapshot captured when the original capture gates pass" testid="saved-plan">
        {!data ? <Empty>Loading…</Empty> : data.active.length === 0 ? <Empty>No saved plan. A plan is captured automatically when F26/F27/F28/F29/F30/F32/F34/F35/F37 pass on a completed 1h breakout.</Empty> : data.active.map((p) => (
          <div key={p.id} data-testid={`plan-${p.id}`}>
            <div className="flex items-center justify-between"><span className="font-mono text-sm text-amber-300">{p.plan_id}</span><Chip status={p.status} /></div>
            {p.status_reason && <Note tone="amber">Paused: {p.status_reason}. The plan stays available for recovery.</Note>}
            <div className="grid sm:grid-cols-2 gap-x-6 mt-2">
              <KV k="Venue / instrument" v={`${p.venue} ${p.instrument}`} /><KV k="Direction" v={<Chip status={p.direction} />} />
              <KV k="Entry / stop / target" v={`${n(p.entry, 1)} / ${n(p.stop, 1)} / ${n(p.target, 1)}`} /><KV k="Quantity" v={`${p.qty_btc} BTC (${p.contracts} ct)`} />
              <KV k="ATR · breakout ref" v={`${n(p.atr, 1)} · ${n(p.breakout_ref, 1)}`} /><KV k="Tick · contract" v={`${p.tick_size} · ${p.contract_value} BTC`} />
              <KV k="Costs" v={`fee ${p.costs.fee_per_side}, slip ${p.costs.slippage_bps} bps, funding ${p.costs.funding_bps} bps`} /><KV k="Net R at capture" v={n(p.net_r, 3)} />
              <KV k="Confirmation bar" v={`${ist(p.confirm_bar_open)} → ${ist(p.confirm_bar_close)}`} /><KV k="Captured / expires" v={`${ist(p.captured_at)} / ${ist(p.expires_at)}`} />
            </div>
            {isAdmin && <button data-testid="cancel-plan" onClick={() => cancel(p.id)} className="mt-3 rounded border border-red-500/40 px-3 py-1.5 text-xs text-red-300 hover:bg-red-500/10">Cancel plan</button>}
          </div>
        ))}
      </Card>
      <Card title="Plan archive" sub="Expired, invalidated, consumed or cancelled — fixed snapshots preserved" testid="plan-archive">
        {!data?.archive?.length ? <Empty>No archived plans yet.</Empty> : (
          <div className="overflow-x-auto scroll-thin"><table className="desk-table"><thead><tr><th>Plan</th><th>Status</th><th>Entry/Stop/Target</th><th>Captured</th><th>Archived</th><th>Reason</th></tr></thead>
            <tbody>{data.archive.map((p) => <tr key={p.id}><td className="font-mono">{p.plan_id}</td><td><Chip status={p.status} /></td><td className="font-mono">{n(p.entry, 1)} / {n(p.stop, 1)} / {n(p.target, 1)}</td><td className="font-mono text-[11px]">{ist(p.captured_at)}</td><td className="font-mono text-[11px]">{ist(p.archived_at)}</td><td className="text-xs text-slate-400">{p.status_reason}</td></tr>)}</tbody></table></div>
        )}
      </Card>
    </>
  );
}

export default function StrategyPage({ strat }) {
  const { state: s, isAdmin } = useDesk();
  const ev = s?.[strat];
  if (!ev) return <Empty>Waiting for first calculation…</Empty>;
  const sc = strat === "scalp";
  const totalChecks = ev.total_checks ?? ev.checks?.length ?? 0;
  const scenarios = Object.entries(ev.scenario_levels || {});
  return (
    <div className="space-y-4">
      <PageHead title={sc ? "Scalping Signal" : "Swing Signal"} sub={sc ? "Completed 5-minute candles with a 15-minute trend filter. Momentum: close vs VWAP20, close near candle extreme (0.3×ATR), volume ratio ≥ 0.9." : "Completed 1-hour candles with a 4-hour trend filter. Confirmation: close beyond prior-20 range, beyond VWAP20, volume ratio ≥ 1."}
        right={<Chip status={ev.status} testid="strategy-status" className="text-sm px-4 py-1.5" />} />
      <div className="grid gap-4 lg:grid-cols-[1fr_1.2fr]">
        <Card title="Signal & levels" sub={`${ev.passed}/${totalChecks} checks · mandatory gates ${ev.mandatory_pass ? "PASS" : "not all passing"}`} testid="strategy-levels-card">
          <Levels lv={ev.levels} testid={`${strat}-page-levels`} />
          {ev.paused_plan && <Note tone="amber">A saved plan is paused ({ev.paused_plan.status_reason}); execution is on hold until bars are continuous again.</Note>}
          <p className="mt-3 text-[11px] text-slate-500">PLAN ONLY / READY TO CAPTURE are not trade instructions. BUY/SELL appears and is auto-captured only when all {totalChecks} active checks pass. An open actual position switches this strategy to MONITOR. F31 review/journal is advisory and excluded from eligibility; its records are retained.</p>
        </Card>
        <Card title={`${totalChecks} active checks`} sub="Tap a check to see its calculation and any missing prerequisites" testid="strategy-checks-card"><ChecksList ev={ev} testid={`${strat}-checks`} /></Card>
      </div>
      {scenarios.length > 0 && <Card title="F35–F37 scenario calculations" sub="Derived from current venue quotes, contract specifications and completed candles" testid="scenario-levels">
        <Note tone="amber">No qualified direction yet. These LONG and SHORT scenarios are diagnostic previews, cannot be executed, and do not make F35–F37 pass.</Note>
        <div className="grid gap-3 mt-3 lg:grid-cols-2">
          {scenarios.map(([side, lv]) => <div key={side} className="rounded border border-[#1e2a4a] p-3" data-testid={`scenario-${side.toLowerCase()}`}>
            <div className="font-mono text-sm text-amber-300 mb-2">{side} scenario · NOT EXECUTABLE</div>
            <KV k="Entry / stop / target" v={`${n(lv.entry, 1)} / ${n(lv.stop, 1)} / ${n(lv.target, 1)}`} />
            <KV k="F35 · net payoff" v={`${n(lv.net_r, 3)} R after costs`} />
            <KV k="F36 · distance / ATR" v={`${n(Math.abs(lv.entry - lv.close), 1)} / ${n(lv.atr, 1)} USD`} />
            <KV k="F37 · quantity" v={`${n(lv.qty_btc, 6)} BTC (${lv.contracts ?? "—"} contracts)`} />
            <p className="mt-2 font-mono text-[10.5px] text-slate-500">Quote {ist(lv.quote_time)} · {lv.quote_source || "venue quote"}{lv.bar_close ? ` · completed bar ${ist(new Date(lv.bar_close * 1000).toISOString())}` : ""}</p>
            <p className="mt-1 text-[11px] text-slate-500">{lv.note}</p>
          </div>)}
        </div>
      </Card>}
      <Card title="Indicator frames" testid="frames-card"><FrameTable frames={ev.frames} /></Card>
      <Card title="Price chart" testid="strategy-chart"><CandleChart tf={sc ? "5m" : "1h"} levels={ev.levels} testid={`${strat}-chart`} /></Card>
      {!sc && <PlanPanel isAdmin={isAdmin} />}
    </div>
  );
}
