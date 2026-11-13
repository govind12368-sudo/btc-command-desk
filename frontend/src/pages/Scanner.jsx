import React from "react";
import { useFetch } from "@/hooks/useFetch";
import { Card, PageHead, Note } from "@/components/desk/ui";
import { n, ist, ago } from "@/lib/fmt";
import { DayTypeCard, EvidenceCard } from "@/components/desk/Books";

const DEC_TONE = { BUY: "text-teal-300", SELL: "text-red-300", WAIT: "text-amber-300" };
const VOTE = { 1: ["▲ buy", "text-teal-300"], "-1": ["▼ sell", "text-red-300"], 0: ["— neutral", "text-slate-400"] };

function Decision({ title, sub, d, testid }) {
  if (!d) return null;
  return (
    <Card title={title} sub={sub} testid={testid}
      right={<span className={`font-mono text-2xl font-bold ${DEC_TONE[d.decision]}`} data-testid={`${testid}-decision`}>{d.decision}</span>}>
      <p className="mb-2 text-[12.5px] text-slate-300">{d.for_buy} factor{d.for_buy === 1 ? "" : "s"} for buy, {d.for_sell} for sell, {d.neutral} neutral (score {d.score > 0 ? "+" : ""}{d.score}). BUY needs +2 or more, SELL needs −2 or less.</p>
      <div className="overflow-x-auto scroll-thin">
        <table className="desk-table">
          <thead><tr><th>Group</th><th>Factor</th><th>Vote</th><th>Reading</th><th>Data</th><th>Age</th></tr></thead>
          <tbody>
            {d.factors.map((f) => (
              <tr key={f.group + f.name}>
                <td className="text-slate-400">{f.group}</td><td>{f.name}</td><td className={`whitespace-nowrap font-semibold ${VOTE[f.vote][1]}`}>{VOTE[f.vote][0]}</td>
                <td className="text-slate-300">{f.reading}</td><td className="text-[11.5px] text-slate-400">{f.source}</td><td className="whitespace-nowrap font-mono text-[11px] text-slate-500">{f.at ? ago(f.at) : "—"}</td>
              </tr>
            ))}
            {!d.factors.length && <tr><td colSpan={6} className="py-4 text-center text-slate-500">No factor has data yet.</td></tr>}
          </tbody>
        </table>
      </div>
    </Card>
  );
}

const Score = ({ label, s }) => (
  <div className="rounded-lg border border-[#1e2a4a] bg-[#0f172a]/60 px-3 py-2.5">
    <div className="text-[11.5px] text-slate-400">{label}</div>
    <div className="font-mono text-lg font-bold text-slate-50">{s?.checked ? `${s.right} of ${s.checked} right (${s.pct}%)` : "No checked calls yet"}</div>
  </div>
);

export default function Scanner() {
  const { data: d } = useFetch("/scanner", 30000);
  if (!d) return <div className="text-sm text-slate-400">Loading the scanner…</div>;
  const L = d.levels || {};
  const lv = [["12-hour high", L.high_12h], ["12-hour VWAP", L.vwap_12h], ["12-hour low", L.low_12h], ["Yesterday high", L.yesterday_high], ["Yesterday low", L.yesterday_low], ["Today open (IST)", L.today_open], ["Average daily range", L.adr]];
  return (
    <div className="space-y-4" data-testid="scanner-page">
      <PageHead title="BTC Scanner" sub="A desk-style read: the data, the day type, then the plan. Decision support only; it places no orders." />
      <Note>These calls are not proven yet. The score card below checks every call against what price did next, and the long test shows the day type alone has not predicted direction.</Note>
      <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
        <Decision title="Today (next 24 hours)" sub="Structure, order flow, leverage and options." d={d.intraday} testid="scan-intraday" />
        <Decision title="Swing (next several days)" sub="Daily trend, institutional money, macro and options positioning." d={d.swing} testid="scan-swing" />
      </div>
      <DayTypeCard day={d.day_type} />
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <Card title="Plan for the day type" sub={d.playbook} testid="scan-levels">
          <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
            {lv.map(([k, v]) => (
              <div key={k} className="rounded-lg border border-[#1e2a4a] bg-[#0f172a]/60 px-3 py-2"><div className="text-[11.5px] text-slate-400">{k}</div><div className="font-mono text-base font-bold text-slate-50">{v == null ? "—" : n(v, 0)}</div></div>
            ))}
          </div>
          <p className="mt-2 text-[11.5px] text-slate-500">A break of the 12-hour high or low that holds for an hour is the usual sign that a range is ending.</p>
        </Card>
        <Card title="Score card" sub={`Every hour's call is saved (${d.scorecard?.hours_logged || 0} hours so far) and checked later against the price.`} testid="scan-score">
          <div className="grid grid-cols-1 gap-2">
            <Score label="Today view: BUY/SELL right after 24 hours" s={d.scorecard?.intraday} />
            <Score label="Swing view: BUY/SELL right after 5 days" s={d.scorecard?.swing} />
            <Score label="Trend call: price further the same way after 6 hours" s={d.scorecard?.trend_continued_6h} />
          </div>
        </Card>
      </div>
      <Card title="Data sources" sub="All free and public. A source that fails is left out of the vote, never guessed." testid="scan-sources">
        <div className="overflow-x-auto scroll-thin">
          <table className="desk-table">
            <thead><tr><th>Source</th><th>Status</th><th>Last update</th><th>Problem</th></tr></thead>
            <tbody>
              {(d.sources || []).map((s) => (
                <tr key={s.name}><td>{s.name}</td><td className={s.ok ? "font-semibold text-teal-300" : "font-semibold text-red-300"}>{s.ok ? "Working" : "Not working"}</td>
                  <td className="font-mono text-[11.5px]">{s.updated ? ist(s.updated) : "—"}</td><td className="text-[11.5px] text-slate-400">{s.ok ? "" : s.error}</td></tr>
              ))}
            </tbody>
          </table>
        </div>
        <p className="mt-2 text-[11.5px] text-slate-500">Order flow, leverage and macro come from the site's own pillars (Delta and OKX live data, US Federal Reserve series).</p>
      </Card>
      <Card title="How a professional desk works" testid="scan-method">
        <ol className="list-decimal space-y-1 pl-5 text-[13px] text-slate-200">
          <li><b>Read the data:</b> flows, positioning, key levels, the event calendar.</li>
          <li><b>Call the day type:</b> trend or range. It is a description that is updated through the day, not a forecast.</li>
          <li><b>Write the plan:</b> what to do if price holds a level, and the level that proves the plan wrong.</li>
          <li><b>Fix the risk:</b> size and the most that can be lost today.</li>
          <li><b>Review:</b> check every call afterwards. That is the score card on this page.</li>
        </ol>
      </Card>
      <EvidenceCard ev={d.evidence} />
    </div>
  );
}
