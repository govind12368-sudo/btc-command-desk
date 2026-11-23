import React, { useMemo, useState } from "react";
import { useFetch } from "@/hooks/useFetch";
import { Card, PageHead, Chip, KpiStrip, Tabs } from "@/components/desk/ui";
import { ist } from "@/lib/fmt";

const TONE = { OK: "PASS", PARTIAL: "PASS", "ON DEMAND": "IDLE", WAITING: "WAIT", LATE: "WAIT", "ACCESS NEEDED": "WAIT", STALE: "BLOCKED", ERROR: "BLOCKED" };
const age = (s) => (s == null ? "—" : s < 90 ? `${Math.round(s)}s` : s < 5400 ? `${Math.round(s / 60)}m` : s < 172800 ? `${(s / 3600).toFixed(1)}h` : `${Math.round(s / 86400)}d`);
const every = (s) => (!s ? "stream" : s < 60 ? `${s}s` : s < 3600 ? `${Math.round(s / 60)}m` : `${Math.round(s / 3600)}h`);

function Row({ x, open, onClick }) {
  return (
    <>
      <tr onClick={onClick} className="cursor-pointer">
        <td><Chip status={TONE[x.status] || "WAIT"} label={x.status} /></td>
        <td className="text-slate-200">{x.name}{x.family && <span className="ml-2 font-mono text-[10.5px] text-slate-500">{x.family}</span>}</td>
        <td className="num">{age(x.age_sec)}</td>
        <td className="num text-slate-400">{every(x.every_sec)}</td>
        <td className="num text-slate-400">{x.fails ?? "—"}</td>
        <td className="max-w-[420px] truncate text-[12px] text-slate-400">{x.error || ""}</td>
      </tr>
      {open && (x.error || x.next_retry || x.last_run) && (
        <tr><td colSpan={6} className="bg-panel2 text-[12px] text-slate-300">
          {x.error && <div><span className="text-slate-500">Last error: </span>{x.error}</div>}
          {x.next_retry && <div><span className="text-slate-500">Next retry: </span>{ist(x.next_retry)}</div>}
          {x.last_run && <div><span className="text-slate-500">Last run: </span>{ist(x.last_run)}</div>}
        </td></tr>
      )}
    </>
  );
}

function Table({ rows, testid }) {
  const [open, setOpen] = useState(null);
  return (
    <div className="max-h-[560px] overflow-auto scroll-thin" data-testid={testid}>
      <table className="desk-table">
        <thead><tr><th>Status</th><th>Feed / loop</th><th className="num">Last update</th><th className="num">Every</th><th className="num">Fails</th><th>Problem</th></tr></thead>
        <tbody>{rows.map((x) => <Row key={x.key} x={x} open={open === x.key} onClick={() => setOpen(open === x.key ? null : x.key)} />)}</tbody>
      </table>
    </div>
  );
}

export default function DataHealth() {
  const { data: d, error } = useFetch("/data-health", 15000, { topic: null });
  const [filter, setFilter] = useState("all");
  const order = { ERROR: 0, STALE: 1, "ACCESS NEEDED": 2, LATE: 3, WAITING: 4, PARTIAL: 5, "ON DEMAND": 6, OK: 7 };
  const feeds = useMemo(() => (d?.feeds || []).filter((x) => filter === "all" || (filter === "problems" ? !["OK", "PARTIAL", "ON DEMAND"].includes(x.status) : true))
    .sort((a, b) => (order[a.status] ?? 9) - (order[b.status] ?? 9)), [d, filter]); // eslint-disable-line react-hooks/exhaustive-deps
  if (error && !d) return <div className="text-[12.5px] text-red-300">Could not load data health: {error}</div>;
  if (!d) return <div className="font-mono text-[12px] text-slate-500">Checking every feed…</div>;
  const s = d.summary, db = d.database, sv = d.server, st = d.stream;
  return (
    <div className="space-y-3" data-testid="data-health-page">
      <PageHead title="Data Health" sub="Every data feed and calculation loop behind the site: when it last updated, how often it should, and what went wrong. Click a row for details." />
      <KpiStrip testid="dh-kpis" items={[
        { label: "Healthy", value: `${s.ok} / ${s.feeds + s.engines}`, cls: s.problems.length ? "text-amber-300" : "text-teal-300" },
        { label: "Problems", value: s.problems.length, cls: s.problems.length ? "text-red-300" : "text-slate-50", sub: s.problems.slice(0, 2).join(", ") || "none" },
        { label: "Late", value: s.late.length, cls: s.late.length ? "text-amber-300" : "text-slate-50", sub: s.late.slice(0, 2).join(", ") || "none" },
        { label: "Database", value: db.status === "OK" ? `${db.ping_ms} ms` : db.status, cls: db.status === "OK" ? "text-teal-300" : "text-red-300", sub: db.size_mb != null ? `${db.size_mb} MB · ${db.collections} collections` : db.error },
        { label: "Live push", value: `${st.clients} open`, sub: st.last_pulse ? `last pulse ${ist(st.last_pulse)}` : "no pages open" },
        { label: "Server", value: `${sv.mem_pct}% mem`, cls: sv.mem_pct > 85 ? "text-red-300" : "text-slate-50", sub: `load ${sv.load[0]} on ${sv.cpus} core · swap ${sv.swap_mb} MB · disk ${sv.disk_pct}%` },
      ]} />
      <Card title="Data feeds" sub={`${d.feeds.length} feeds · checked ${ist(d.at)}`} right={<Tabs value={filter} onChange={setFilter} items={[["all", "All"], ["problems", "Problems only"]]} testid="dh-filter" />} pad={false}>
        <Table rows={feeds} testid="dh-feeds" />
      </Card>
      <Card title="Calculation loops" sub="Strategy engines and journals running on the server" pad={false}>
        <Table rows={d.engines} testid="dh-engines" />
      </Card>
    </div>
  );
}
