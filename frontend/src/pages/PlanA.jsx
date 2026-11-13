import React from "react";
import { toast } from "sonner";
import { useFetch } from "@/hooks/useFetch";
import { useDesk } from "@/context/DeskContext";
import { Card, PageHead, Note } from "@/components/desk/ui";
import { Btn } from "@/components/desk/forms";
import { n, usd, ist } from "@/lib/fmt";
import { LineChart, VIZ } from "@/components/desk/charts";
import { DayChip, DayTypeCard, PLAYBOOK_NAME } from "@/components/desk/Books";

const RES_TONE = { TARGET: "text-teal-300", STOP: "text-red-300", TIME: "text-amber-300", OPEN: "text-sky-300" };

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
      <PageHead title="Plan A – live" sub={`${data?.rule || "4C reverse 1:2 + 6-hour exit"} · updated ${ist(data?.computed_at)} · last Delta 1m close ${n(data?.live_price, 1)}`} />
      {data?.error && <Note>Last update failed: {data.error}. It retries every 2 minutes.</Note>}
      <div className="grid grid-cols-2 gap-2 sm:grid-cols-4 lg:grid-cols-8">
        <Stat label="Signals" value={s.signals ?? "—"} />
        <Stat label="Trades taken" value={s.taken ?? "—"} />
        <Stat label="Skipped (busy)" value={s.skipped ?? "—"} />
        <Stat label="Still open" value={s.open ?? "—"} tone="text-sky-300" />
        <Stat label="Target / Stop / 6h" value={`${s.target ?? 0} / ${s.stop ?? 0} / ${s.time ?? 0}`} />
        <Stat label="Win rate" value={s.win_rate == null ? "—" : `${Math.round(s.win_rate * 100)}%`} />
        <Stat label="Net P&L (est.)" value={usd(s.net)} tone={(s.net || 0) >= 0 ? "text-teal-300" : "text-red-300"} />
        <Stat label="Worst drawdown" value={usd(s.max_drawdown)} tone="text-red-300" />
      </div>
      {curve.length > 1 && (
        <Card title="Cumulative net P&L" sub="Closed Plan A trades, after estimated fees" testid="plan-a-curve">
          <LineChart series={[{ name: "Net P&L $", color: VIZ.s1, points: curve }]} xFmt={(v) => v} yFmt={(v) => n(v, 0)} unit=" $" height={160} />
        </Card>
      )}
      <DayTypeCard day={data?.day_type} compact />
      <Card title="Trades" sub="Newest first. Reverse of each filled scalp signal; skipped when the previous Plan A trade is still open." testid="plan-a-table">
        <div className="overflow-x-auto scroll-thin">
          <table className="desk-table">
            <thead><tr><th>Signal</th><th>Day type at signal</th><th>Plan A</th><th>Fill (IST)</th><th>Entry</th><th>Stop</th><th>Target</th><th>Result</th><th>Exit (IST)</th><th>Net $</th><th>Taken?</th></tr></thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.signal_id}>
                  <td className="font-mono text-[11.5px]">{r.signal_id}</td>
                  <td><DayChip type={r.day_type} /><span className="block text-[10.5px] text-slate-500">{PLAYBOOK_NAME[r.playbook] || ""}</span></td>
                  <td className={r.plan_dir === "LONG" ? "text-teal-300" : r.plan_dir === "SHORT" ? "text-red-300" : "text-slate-500"}>{r.plan_dir || "—"}</td>
                  <td className="font-mono text-[11.5px]">{r.fill_ist || (r.fill_status === "NOT FILLED" ? "not filled" : "waiting")}</td>
                  <td className="font-mono">{n(r.entry, 1)}</td>
                  <td className="font-mono">{r.plan_stop ? n(r.plan_stop, 1) : "—"}</td>
                  <td className="font-mono">{r.plan_target ? n(r.plan_target, 1) : "—"}</td>
                  <td className={`font-semibold ${RES_TONE[r.plan_result] || "text-slate-500"}`}>{r.plan_result || "—"}</td>
                  <td className="font-mono text-[11.5px]">{r.exit_ist || "—"}</td>
                  <td className={`font-mono ${(r.net || 0) >= 0 ? "text-teal-300" : "text-red-300"}`}>{r.net == null ? "—" : usd(r.net)}</td>
                  <td className={r.taken === "Skip" ? "text-slate-500" : "text-slate-200"}>{r.taken || "—"}</td>
                </tr>
              ))}
              {!rows.length && <tr><td colSpan={11} className="py-6 text-center text-slate-500">No scalp signals captured yet. New ones appear here automatically.</td></tr>}
            </tbody>
          </table>
        </div>
        <p className="mt-2 text-[11px] text-slate-500">Fees: entry taker, exit maker on TARGET otherwise taker, +18% GST. Funding and slippage not included. Model rules chosen after looking at past data — expect somewhat worse results on new signals.</p>
      </Card>
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
