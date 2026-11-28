import React from "react";
import { Link } from "react-router-dom";
import { useFetch } from "@/hooks/useFetch";
import { Card, PageHead, KpiStrip, Note, Empty } from "@/components/desk/ui";
import { signedUsd, ist } from "@/lib/fmt";

// Research & Edge (owner, 28 Nov 2026): the data loaded, every strategy search, what passed each check, and the results of the
// strategies that were kept. No strategy rules are shown.
const pnlTone = (v) => (v > 0 ? "text-teal-300" : v < 0 ? "text-red-300" : "text-slate-300");
const pctTxt = (v) => (v == null ? "—" : `${Math.round(v * 100)}%`);
const retTxt = (v) => (v == null ? "—" : `${v >= 0 ? "+" : "−"}${Math.abs(v * 100).toFixed(1)}%`);
const int = (v) => (v == null ? "—" : Number(v).toLocaleString("en-US"));
const day = (s) => {
  if (!s) return "—";
  const d = new Date(`${s.slice(0, 10)}T00:00:00Z`);
  return Number.isNaN(d.getTime()) ? s : d.toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric", timeZone: "UTC" });
};

function DataTable({ rows }) {
  return (
    <div className="overflow-x-auto scroll-thin">
      <table className="desk-table" data-testid="research-data">
        <thead><tr><th>Data</th><th>From</th><th>To</th><th className="num">Years</th><th>Used for</th></tr></thead>
        <tbody>{rows.map((d) => (
          <tr key={d.what}>
            <td className="max-w-[420px] whitespace-normal">{d.what}</td>
            <td className="font-mono">{day(d.from)}</td><td className="font-mono">{day(d.to)}</td>
            <td className="num">{d.years}</td>
            <td className="max-w-[360px] whitespace-normal text-slate-400">{d.used}</td>
          </tr>))}
        </tbody>
      </table>
    </div>
  );
}

function FunnelTable({ rows, totals }) {
  return (
    <div className="overflow-x-auto scroll-thin">
      <table className="desk-table" data-testid="research-funnel">
        <thead><tr><th>Search</th><th>Data</th><th className="num">Versions backtested</th><th>What passed the checks</th><th className="num">Kept</th><th>Kept as</th></tr></thead>
        <tbody>
          {rows.map((f) => (
            <tr key={f.search}>
              <td className="max-w-[260px] whitespace-normal">{f.search}</td>
              <td className="max-w-[200px] whitespace-normal text-slate-400">{f.data}</td>
              <td className="num">{int(f.tested)}</td>
              <td className="max-w-[360px] whitespace-normal text-slate-300">{f.passed}</td>
              <td className={`num font-semibold ${f.kept ? "text-teal-300" : "text-slate-500"}`}>{f.kept}</td>
              <td className="max-w-[260px] whitespace-normal">{f.kept_as}</td>
            </tr>))}
          <tr className="border-t-2 border-line2 font-semibold">
            <td>Total</td><td /><td className="num">{int(totals.tested)}</td>
            <td className="text-slate-400">{totals.searches} searches</td>
            <td className="num text-teal-300">{totals.kept}</td>
            <td className="text-slate-400">about 1 in {int(Math.round(totals.tested / Math.max(totals.kept, 1)))} versions tested</td>
          </tr>
        </tbody>
      </table>
    </div>
  );
}

const VERDICT_TONE = {
  Proven: "border-teal-400/60 bg-teal-500/15 text-teal-200",
  Profitable: "border-amber-400/60 bg-amber-500/10 text-amber-200",
  "Not profitable": "border-red-400/60 bg-red-500/10 text-red-200",
};

function Verdict({ r }) {
  const why = r.status_why || [];
  return (
    <div>
      <span className={`inline-block rounded-sm border px-2 py-0.5 text-[12px] font-semibold ${VERDICT_TONE[r.status] || "border-line text-slate-300"}`}>
        {r.status === "Proven" ? "✓ Proven" : r.status}
      </span>
      {why.length > 0 && <div className="mt-0.5 text-[11px] text-slate-500">{why.join(" · ")}</div>}
      {r.stage && <div className="text-[11px] text-slate-500">{r.stage.toLowerCase().startsWith("paper") ? `in ${r.stage.charAt(0).toLowerCase()}${r.stage.slice(1)}` : r.stage}</div>}
    </div>
  );
}

function KeptTable({ rows }) {
  if (!rows.length) return <Empty>The kept strategies' results are loading; they appear here once each page has computed them.</Empty>;
  return (
    <div className="overflow-x-auto scroll-thin">
      <table className="desk-table" data-testid="research-kept">
        <thead><tr>
          <th>Strategy</th><th>Tested period</th><th className="num">Years</th><th className="num">Trades</th><th className="num">Win rate</th>
          <th className="num">Net P&L after fees</th><th className="num">Capital</th><th className="num" title="Net P&L ÷ capital">Return on capital</th>
          <th className="num" title="Fixed size: return ÷ years tested. Compounded: the yearly growth rate.">A year</th><th>Status</th>
        </tr></thead>
        <tbody>{rows.map((r) => (
          <tr key={r.strategy}>
            <td className="min-w-[220px] max-w-[280px] whitespace-normal">
              {r.page ? <Link to={r.page} className="text-slate-100 hover:text-amber-300">{r.strategy}</Link> : r.strategy}
              {r.source && <div className="text-[11px] font-normal text-slate-400">Found in: {r.source}</div>}
              {r.note && <div className="text-[11px] font-normal text-slate-500">{r.note}</div>}
            </td>
            <td className="whitespace-nowrap font-mono text-[11.5px]">{day(r.from)}<div className="text-slate-500">→ {day(r.to)}</div></td>
            <td className="num">{r.years ? r.years : "—"}</td>
            <td className="num">{int(r.trades)}</td>
            <td className={`num font-semibold ${r.win_rate >= 0.5 ? "text-teal-300" : "text-slate-200"}`}>{pctTxt(r.win_rate)}</td>
            <td className={`num font-semibold ${pnlTone(r.net)}`}>{signedUsd(r.net, 0)}</td>
            <td className="num">{r.capital ? `$${Math.round(r.capital).toLocaleString("en-US")}` : "—"}</td>
            <td className={`num font-semibold ${pnlTone(r.return)}`}>{retTxt(r.return)}</td>
            <td className={`num ${pnlTone(r.per_year)}`}>{retTxt(r.per_year)}{r.compound && r.per_year != null && <div className="text-[10.5px] font-normal text-slate-500">growth rate</div>}</td>
            <td className="min-w-[130px]"><Verdict r={r} /></td>
          </tr>))}
        </tbody>
      </table>
    </div>
  );
}

function Steps({ steps }) {
  return (
    <ol className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3" data-testid="research-steps">
      {steps.map((s, i) => (
        <li key={s.title} className="flex gap-3 rounded-sm border border-line bg-panel2/50 px-3 py-2.5">
          <span className="mt-0.5 flex h-6 w-6 shrink-0 items-center justify-center rounded-full border border-amber-500/50 font-mono text-[12px] text-amber-300">{i + 1}</span>
          <div>
            <div className="text-[13px] font-semibold text-slate-100">{s.title}</div>
            <div className="text-[12px] leading-relaxed text-slate-400">{s.text}</div>
          </div>
        </li>))}
    </ol>
  );
}

export default function Research() {
  const { data: d, error } = useFetch("/research", 120000);
  if (error && !d) return <><PageHead title="Research & Edge" /><Note tone="red">Could not load this page: {String(error)}</Note></>;
  if (!d) return <><PageHead title="Research & Edge" /><Empty>Loading…</Empty></>;
  const t = d.totals || {};
  const kept = d.kept || [];
  const proven = new Set(kept.filter((r) => r.status === "Proven").map((r) => r.page || r.strategy)).size;
  const profitable = new Set(kept.filter((r) => r.net > 0).map((r) => r.page || r.strategy)).size;
  return (
    <div className="space-y-3" data-testid="research-page">
      <PageHead title="Research & Edge"
        sub="How the strategies on this site were found: the data we loaded, every search we ran, what passed each check, and what the strategies we kept have made after fees." />
      <KpiStrip testid="research-kpis" items={[
        { label: "Price history loaded", value: `${t.years} years`, sub: "BTC since 1 Jan 2023" },
        { label: "Strategy versions backtested", value: int(t.tested), sub: `${t.searches} separate searches` },
        { label: "Strategies kept", value: int(t.kept), sub: "now in paper trading", cls: "text-teal-300" },
        kept.length ? { label: "Proven", value: `${proven} of ${profitable}`, sub: "profitable strategies meet every bar", cls: "text-teal-300" } : null,
        { label: "Kept share", value: `1 in ${int(Math.round((t.tested || 0) / Math.max(t.kept || 1, 1)))}`, sub: "of the versions tested" },
      ]} />
      <Card title="Data loaded" sub="Real exchange data, each value as it was known at the time" testid="research-data-card"><DataTable rows={d.data || []} /></Card>
      <Card title="Every search, and what passed" sub="One version = one rule set on one timeframe, with one exit and one direction" testid="research-funnel-card">
        <FunnelTable rows={d.funnel || []} totals={t} />
      </Card>
      <Card title="The strategies we kept: results after fees" sub={d.computed_at ? `read live from each strategy's page · ${ist(d.computed_at)}` : null} testid="research-kept-card">
        <KeptTable rows={d.kept || []} />
        {d.proven_rule && <Note tone="teal" className="mt-2">{d.proven_rule}</Note>}
        <Note tone="plain" className="mt-1">Net P&L counts every fee and 18% GST. Return on capital = net P&L ÷ the capital shown. A year = that return ÷ the years tested (for the compounded row, the yearly growth rate). Shorter than half a year: not shown.</Note>
      </Card>
      <Card title="How a strategy earns its place" testid="research-steps-card"><Steps steps={d.steps || []} /></Card>
      <Note tone="amber">{d.note}</Note>
    </div>
  );
}
