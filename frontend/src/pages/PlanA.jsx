import React from "react";
import { toast } from "sonner";
import { useFetch } from "@/hooks/useFetch";
import { useDesk } from "@/context/DeskContext";
import { Card, PageHead, Note } from "@/components/desk/ui";
import { Btn } from "@/components/desk/forms";
import { n, usd, signedUsd, ist } from "@/lib/fmt";
import { LineChart, VIZ } from "@/components/desk/charts";
import { DayChip, DayTypeCard, PLAYBOOK_NAME } from "@/components/desk/Books";
import { SignalCell, ResultCell, FillCell, NetCell, PriceCell, WhyCell, DIR_TONE } from "@/components/desk/TradeCells";

function Stat({ label, value, tone = "text-slate-50" }) {
  return (
    <div className="rounded-lg border border-[#1e2a4a] bg-[#0f172a]/60 px-3 py-2.5">
      <div className="text-[11.5px] text-slate-400">{label}</div>
      <div className={`font-mono text-lg font-bold ${tone}`}>{value}</div>
    </div>
  );
}

export default function PlanA() {
  const { isAdmin } = useDesk();
  const { data } = useFetch("/plan-a", 60000);
  const s = data?.summary || {};
  const taken = (data?.rows || []).filter((r) => r.taken === "Yes");
  let eq = 0;
  const curve = taken.filter((r) => r.plan_result !== "OPEN").map((r) => ({ t: r.exit_ist, v: (eq += r.net) }));
  const copy = async () => { try { await navigator.clipboard.writeText(data.feed_url); toast.success("Feed link copied"); } catch { toast.error("Copy failed — select the link and copy it"); } };
  const rows = [...(data?.rows || [])].reverse();
  return (
    <div className="space-y-4" data-testid="plan-a-page">
      <PageHead title="Plan A – live" sub={`Reverse of every scalp signal, 1:2, 6-hour exit, one trade at a time · updated ${ist(data?.computed_at)} · BTC ${n(data?.live_price, 1)}`} />
      {data?.error && <Note>Last update failed: {data.error}. It retries every 2 minutes.</Note>}
      <div className="grid grid-cols-2 gap-2 sm:grid-cols-4 lg:grid-cols-8">
        <Stat label="Signals" value={s.signals ?? "—"} />
        <Stat label="Trades taken" value={s.taken ?? "—"} />
        <Stat label="Skipped (busy)" value={s.skipped ?? "—"} />
        <Stat label="Still open" value={s.open ?? "—"} tone="text-sky-300" />
        <Stat label="Target / Stop / 6h" value={`${s.target ?? 0} / ${s.stop ?? 0} / ${s.time ?? 0}`} />
        <Stat label="Win rate" value={s.win_rate == null ? "—" : `${Math.round(s.win_rate * 100)}%`} />
        <Stat label="Net P&L (est.)" value={signedUsd(s.net)} tone={(s.net || 0) >= 0 ? "text-teal-300" : "text-red-300"} />
        <Stat label="Worst drawdown" value={usd(s.max_drawdown)} tone="text-red-300" />
      </div>
      {curve.length > 1 && (
        <Card title="Cumulative net P&L" sub="Closed Plan A trades, after estimated fees" testid="plan-a-curve">
          <LineChart series={[{ name: "Net P&L $", color: VIZ.s1, points: curve }]} xFmt={(v) => v} yFmt={(v) => n(v, 0)} unit=" $" height={160} />
        </Card>
      )}
      <Card title="Trades" sub="Newest first. Each filled scalp signal is traded in the opposite direction: stop = the signal's stop gap, target = 2 x that gap, closed after 6 hours. While a Plan A trade is open, new signals are skipped." testid="plan-a-table">
        <div className="overflow-x-auto scroll-thin">
          <table className="desk-table">
            <thead><tr><th>Signal</th><th>Signal → Plan A</th><th>Day type</th><th className="num">Entry</th><th className="num">Stop</th><th className="num">Target</th><th>Filled</th><th>Result</th><th className="num">Net</th><th>Taken</th><th>Why it won or lost</th></tr></thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.signal_id} className={r.taken === "Skip" ? "opacity-60" : ""}>
                  <SignalCell id={r.signal_id} captured={(r.captured_ist || "").slice(5).replace("-", "/")} />
                  <td className="whitespace-nowrap text-[12px]"><span className={DIR_TONE[r.model_dir]}>{r.model_dir}</span> <span className="text-slate-500">→</span> <span className={`font-semibold ${DIR_TONE[r.plan_dir] || "text-slate-500"}`}>{r.plan_dir || "—"}</span></td>
                  <td><DayChip type={r.day_type} /><span className="block text-[10.5px] text-slate-500">{PLAYBOOK_NAME[r.playbook] || ""}</span></td>
                  <PriceCell v={r.entry} />
                  <PriceCell v={r.plan_stop} />
                  <PriceCell v={r.plan_target} />
                  <FillCell status={r.fill_status} time={r.fill_ist} />
                  <ResultCell result={r.fill_status === "FILLED" ? r.plan_result : null} exit={r.exit_ist} />
                  <NetCell v={r.fill_status === "FILLED" ? r.net : null} />
                  <td className={`whitespace-nowrap text-[12px] ${r.taken === "Yes" ? "text-slate-200" : "text-slate-500"}`}>{r.taken === "Yes" ? "Yes" : r.taken === "Skip" ? "Skipped (busy)" : "—"}</td>
                  <WhyCell why={r.fill_status === "FILLED" ? r.plan_why : r.fill_status === "NOT FILLED" ? "Price never reached the entry within 10 minutes, so no trade." : "Waiting for the entry price."} context={r.context} />
                </tr>
              ))}
              {!rows.length && <tr><td colSpan={11} className="py-6 text-center text-slate-500">No scalp signals captured yet. New ones appear here automatically.</td></tr>}
            </tbody>
          </table>
        </div>
        <p className="mt-2 text-[11px] text-slate-500">"Stop gap" = distance from entry to stop. Net is after fees: entry taker, exit maker on Target otherwise taker, +18% GST. Funding and slippage not included.</p>
      </Card>
      <DayTypeCard day={data?.day_type} compact />
      {isAdmin && data?.feed_url && (
        <Card title="Google Sheet link" sub="Paste this into cell B1 of the 'Vantage Feed' tab in your Vantage Google Sheet. Keep it private — anyone with it can read this table." testid="plan-a-feed">
          <div className="flex flex-wrap items-center gap-2">
            <code className="max-w-full truncate rounded bg-[#0f172a] px-2 py-1.5 text-[11.5px] text-slate-300">{data.feed_url}</code>
            <Btn onClick={copy} data-testid="plan-a-copy">Copy link</Btn>
          </div>
        </Card>
      )}
    </div>
  );
}
