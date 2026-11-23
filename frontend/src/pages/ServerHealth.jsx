import React, { useState } from "react";
import { useFetch } from "@/hooks/useFetch";
import { Card, PageHead, Note } from "@/components/desk/ui";
import { n, ist } from "@/lib/fmt";
import { LineChart, VIZ } from "@/components/desk/charts";

const GB = 1024 ** 3;
const MBy = 1024 ** 2;
const size = (b) => (b == null ? "—" : b >= GB ? `${(b / GB).toFixed(2)} GB` : b >= MBy ? `${(b / MBy).toFixed(0)} MB` : `${(b / 1024).toFixed(0)} KB`);
const pct = (v, d = 0) => (v == null ? "—" : `${Number(v).toFixed(d)}%`);
const up = (s) => { const d = Math.floor(s / 86400), h = Math.floor((s % 86400) / 3600); return d ? `${d} d ${h} h` : `${h} h ${Math.floor((s % 3600) / 60)} min`; };
const tone = (p, warn, bad) => (p >= bad ? "bg-red-500" : p >= warn ? "bg-amber-400" : "bg-teal-400");
const label = (p, warn, bad) => (p >= bad ? "Tight" : p >= warn ? "Watch" : "Plenty of room");

function Meter({ title, used, total, p, warn, bad, lines, testid }) {
  return (
    <div className="rounded-lg border border-[#1e2a4a] bg-[#0f172a]/60 p-3" data-testid={testid}>
      <div className="flex items-baseline justify-between">
        <div className="text-[12px] text-slate-400">{title}</div>
        <div className={`text-[11px] font-semibold ${p >= bad ? "text-red-300" : p >= warn ? "text-amber-300" : "text-teal-300"}`}>{label(p, warn, bad)}</div>
      </div>
      <div className="mt-1 font-mono text-xl font-bold text-slate-50">{pct(p)} <span className="text-[12px] font-normal text-slate-400">{used} of {total}</span></div>
      <div className="mt-2 h-2.5 rounded bg-[#1e2a4a]"><div className={`h-2.5 rounded ${tone(p, warn, bad)}`} style={{ width: `${Math.min(100, Math.max(1, p || 0))}%` }} /></div>
      <ul className="mt-2 space-y-0.5 text-[11.5px] text-slate-400">{lines.map((l) => <li key={l}>{l}</li>)}</ul>
    </div>
  );
}

function Share({ rows, total, unit }) {
  // one horizontal stacked bar: who uses the resource
  const sum = rows.reduce((a, r) => a + (r.v || 0), 0);
  return (
    <div>
      <div className="flex h-4 w-full overflow-hidden rounded bg-[#1e2a4a]">
        {rows.map((r) => <div key={r.name} title={`${r.name}: ${r.txt}`} style={{ width: `${Math.max(0, (r.v || 0) / total * 100)}%`, background: r.color }} className="border-r-2 border-[#0f172a] last:border-r-0" />)}
      </div>
      <table className="desk-table mt-2">
        <thead><tr><th>Part of the site</th><th className="num">Now</th><th className="num">Share of the server</th><th>What it is</th></tr></thead>
        <tbody>{rows.map((r) => (
          <tr key={r.name}><td><span className="mr-2 inline-block h-2.5 w-2.5 rounded-sm align-middle" style={{ background: r.color }} />{r.name}</td>
            <td className="num">{r.txt}</td><td className="num">{pct((r.v || 0) / total * 100, 1)}</td><td className="text-[12px] text-slate-400">{r.what}</td></tr>))}
          <tr className="border-t-2 border-[#2a3a66] font-semibold"><td>Free</td><td className="num">{unit(total - sum)}</td><td className="num">{pct((total - sum) / total * 100, 1)}</td><td /></tr>
        </tbody>
      </table>
    </div>
  );
}

export default function ServerHealth() {
  const { data: d, error } = useFetch("/admin/server", 30000);
  const [range, setRange] = useState("24h");
  if (error && !d) return <Note tone="red">Could not read the server: {String(error)}</Note>;
  if (!d) return <div className="text-sm text-slate-400">Reading the server…</div>;
  const s = d.now, be = s.backend, mg = s.mongo;
  const memP = s.mem_used / s.mem_total * 100, diskP = s.disk_used / s.disk_total * 100;
  const sm = d.summary[range];
  const other = Math.max(0, s.mem_used - (be.rss || 0) - (mg.resident || 0));
  const cutoff = Date.now() - (range === "24h" ? 86400e3 : 7 * 86400e3);
  const hist = d.history.filter((r) => Date.parse(r.at) >= cutoff);
  const pts = (k, f = (v) => v) => hist.map((r) => ({ t: r.at, v: r[k] == null ? null : f(r[k]) }));
  const verdict = [];
  if (memP >= 85) verdict.push("memory is nearly full");
  if (diskP >= 80) verdict.push("the disk is getting full");
  if ((sm.host_cpu.avg ?? 0) >= 70) verdict.push("the CPU is busy most of the time");
  if ((s.swap_used || 0) > 256 * MBy) verdict.push("the server is using swap");
  if ((sm.steal.avg ?? 0) >= 10) verdict.push("the host is taking CPU away (steal time)");
  return (
    <div className="space-y-4" data-testid="server-page">
      <PageHead title="Server" sub={`${s.cpus} CPU cores · ${size(s.mem_total)} memory · ${size(s.disk_total)} disk · up ${up(s.uptime_sec)} · read ${ist(s.at)} · refreshes every 30 s`} />
      <Note tone={verdict.length ? "amber" : undefined}>{verdict.length ? `Needs attention: ${verdict.join("; ")}.` : "The server has plenty of room for this site right now: memory, disk and CPU are all well inside their limits."}</Note>

      <div className="grid grid-cols-1 gap-3 md:grid-cols-2 xl:grid-cols-4">
        <Meter testid="meter-cpu" title="CPU (whole server, last second)" p={s.host_cpu} warn={60} bad={85} used={`load ${s.load[0]}`} total={`${s.cpus} cores`}
          lines={[`${range} average ${pct(sm.host_cpu.avg, 1)}, peak ${pct(sm.host_cpu.max, 0)} (5-second samples)`, `Load 1 / 5 / 15 min: ${s.load.join(" / ")} (busy when above ${s.cpus})`, `Steal (taken by the host): ${pct(s.steal, 1)}`]} />
        <Meter testid="meter-mem" title="Memory" p={memP} warn={75} bad={90} used={size(s.mem_used)} total={size(s.mem_total)}
          lines={[`Available now: ${size(s.mem_available)}`, `${range} peak used: ${size(sm.mem_used.max)}`, `Swap used: ${size(s.swap_used)} of ${size(s.swap_total)}`]} />
        <Meter testid="meter-disk" title="Disk" p={diskP} warn={70} bad={85} used={size(s.disk_used)} total={size(s.disk_total)}
          lines={[`Free: ${size(s.disk_free)}`, `Database on disk: ${size((mg.storage_size || 0) + (mg.index_size || 0))}`,
            d.disk_growth_per_day ? `Growth: disk ${size(d.disk_growth_per_day.disk)} a day, database ${size(d.disk_growth_per_day.database)} a day (over ${d.disk_growth_per_day.days} days)` : "Growth a day: shown after one day of samples"]} />
        <Meter testid="meter-app" title="App responsiveness" p={Math.min(100, (be.loop_delay_ms || 0) / 5)} warn={20} bad={60} used={`${be.loop_delay_ms} ms delay`} total="500 ms"
          lines={[`${range} average delay ${sm.lag.avg ?? "—"} ms, worst ${sm.lag.max ?? "—"} ms`, `${be.tasks} background tasks · ${be.threads} threads · ${be.open_files} open files`, `MongoDB ${mg.version || "—"}: ${mg.connections ?? "—"} connections`]} />
      </div>

      <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
        <Card title="Who uses the memory" sub="Measured from inside the app. 'Everything else' is the web server (Caddy), Docker and Linux itself." testid="mem-share">
          <Share total={s.mem_total} unit={size} rows={[
            { name: "Backend (the app: prices, strategies, journals, Auto-Trade, Elite)", v: be.rss, txt: size(be.rss), color: VIZ.s1, what: `${range} peak ${size(sm.be_mem.max)}` },
            { name: "MongoDB (database)", v: mg.resident, txt: size(mg.resident), color: VIZ.s2, what: `cache ${size(mg.cache_used)} of ${size(mg.cache_max)} limit; ${range} peak ${size(sm.mg_mem.max)}` },
            { name: "Everything else", v: other, txt: size(other), color: VIZ.neutral, what: "web server, Docker, Linux, file cache that cannot be freed" },
          ]} />
        </Card>
        <Card title="Who uses the CPU" sub={`Share of all ${s.cpus} cores over the last second; ${range} averages from the 5-minute samples.`} testid="cpu-share">
          <Share total={100} unit={(v) => pct(v, 1)} rows={[
            { name: "Backend (the app)", v: be.cpu, txt: pct(be.cpu, 1), color: VIZ.s1, what: `${range} average ${pct(sm.be_cpu.avg, 1)}, peak ${pct(sm.be_cpu.max, 1)}` },
            { name: "MongoDB (database)", v: mg.cpu, txt: pct(mg.cpu, 1), color: VIZ.s2, what: `${range} average ${pct(sm.mg_cpu.avg, 1)}, peak ${pct(sm.mg_cpu.max, 1)}` },
            { name: "Everything else", v: Math.max(0, (s.host_cpu || 0) - (be.cpu || 0) - (mg.cpu || 0)), txt: pct(Math.max(0, (s.host_cpu || 0) - (be.cpu || 0) - (mg.cpu || 0)), 1), color: VIZ.neutral, what: "web server, Docker, Linux, auto-update" },
          ]} />
        </Card>
      </div>

      <div className="flex gap-2">
        {["24h", "7d"].map((r) => <button key={r} onClick={() => setRange(r)} className={`rounded px-3 py-1.5 text-xs font-semibold ${range === r ? "bg-amber-500 text-slate-950" : "border border-[#1e2a4a] text-slate-300"}`} data-testid={`range-${r}`}>{r === "24h" ? "Last 24 hours" : "Last 7 days"}</button>)}
        <span className="self-center text-[11.5px] text-slate-500">{hist.length} samples (one every 5 minutes, kept 8 days; history starts when this page was added)</span>
      </div>
      <div className="grid grid-cols-1 gap-4 xl:grid-cols-3">
        <Card title="CPU %" sub="Whole server (blue) and the backend alone (orange)">
          <LineChart height={150} unit="%" yFmt={(v) => n(v, 0)} series={[{ name: "Server CPU %", color: VIZ.s1, points: pts("host_cpu") }, { name: "Backend CPU %", color: VIZ.s2, points: pts("be_cpu") }]} />
        </Card>
        <Card title="Memory used (GB)" sub={`Whole server (blue) of ${size(s.mem_total)}; backend + database (orange)`}>
          <LineChart height={150} unit=" GB" yFmt={(v) => v.toFixed(2)} series={[{ name: "Server memory GB", color: VIZ.s1, points: pts("mem_used", (v) => v / GB) },
            { name: "Backend + MongoDB GB", color: VIZ.s2, points: hist.map((r) => ({ t: r.at, v: ((r.be_mem || 0) + (r.mg_mem || 0)) / GB })) }]} />
        </Card>
        <Card title="Disk used (GB)" sub={`Of ${size(s.disk_total)}`}>
          <LineChart height={150} unit=" GB" yFmt={(v) => v.toFixed(1)} series={[{ name: "Disk used GB", color: VIZ.s1, points: pts("disk_used", (v) => v / GB) }]} />
        </Card>
      </div>

      <Card title="Database" sub={`${mg.collections ?? "—"} collections · ${n(mg.objects, 0)} documents · data ${size(mg.data_size)} · on disk ${size(mg.storage_size)} + indexes ${size(mg.index_size)}`} testid="db-collections">
        <div className="overflow-x-auto scroll-thin">
          <table className="desk-table">
            <thead><tr><th>Largest collections</th><th className="num">Documents</th><th className="num">Data</th><th className="num">On disk (with indexes)</th></tr></thead>
            <tbody>{d.collections.map((c) => <tr key={c.name}><td className="font-mono">{c.name}</td><td className="num">{n(c.count, 0)}</td><td className="num">{size(c.data)}</td><td className="num">{size(c.disk)}</td></tr>)}</tbody>
          </table>
        </div>
        {mg.error && <div className="mt-2 text-[11.5px] text-amber-300">MongoDB statistics partly unavailable: {mg.error}</div>}
      </Card>
      <div className="text-[11.5px] text-slate-500">Linux kernel {d.os.kernel} · {d.os.arch}. Read from inside the backend container: CPU, memory, load and disk are the whole server's; the backend and MongoDB figures are their own.</div>
    </div>
  );
}
