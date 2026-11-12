import React, { useState } from "react";
import { api } from "@/lib/api";
import { Card, Chip, Note } from "./ui";
import { Btn } from "./forms";
import { ist } from "@/lib/fmt";

function RefArchive() {
  const [sheets, setSheets] = useState(null);
  const [sel, setSel] = useState(null);
  const [rows, setRows] = useState(null);
  const [page, setPage] = useState(1);
  const [err, setErr] = useState(null);
  const load = async () => { try { setSheets((await api.get("/reference/sheets")).data); } catch (e) { setErr(e.message); } };
  const open = async (name, source, pg = 1) => {
    setSel({ name, source }); setPage(pg); setErr(null);
    try { setRows((await api.get(`/reference/sheet?name=${encodeURIComponent(name)}&source=${source}&page=${pg}&size=50`)).data); } catch (e) { setErr(e.message); }
  };
  if (!sheets) return <div className="mt-3"><Btn tone="ghost" data-testid="load-reference" onClick={load}>Load read-only reference archive</Btn>{err && <span className="ml-2 text-xs text-red-300">{err}</span>}</div>;
  return (
    <div className="mt-4 rounded border border-[#1e2a4a] p-3" data-testid="reference-archive">
      <p className="mb-2 text-[11px] text-slate-500">{sheets.note}</p>
      <div className="flex flex-wrap gap-1.5">
        {Object.entries(sheets.sheets).map(([k, v]) => (
          <span key={k} className="flex gap-1">
            <Btn tone="ghost" data-testid={`ref-${k}`} onClick={() => open(k, "primary")}>{k} ({v.primary || 0})</Btn>
            {v["backup-unique"] ? <Btn tone="teal" data-testid={`ref-backup-${k}`} onClick={() => open(k, "backup-unique")}>backup-only {v["backup-unique"]}</Btn> : null}
          </span>
        ))}
      </div>
      {err && <p className="mt-2 text-xs text-red-300">Archive request failed: {err}</p>}
      {rows && (
        <div className="mt-3">
          <div className="mb-1 font-mono text-xs text-amber-300">{sel.name} · {sel.source} · {rows.total} rows</div>
          <div className="max-h-[360px] overflow-auto scroll-thin"><table className="desk-table"><tbody>
            {rows.rows.map((r) => <tr key={r.row}><td className="font-mono text-slate-500">{r.row}</td>{r.values.slice(0, 14).map((c, i) => <td key={i} className="font-mono text-[11px] whitespace-nowrap">{c === null ? "" : String(c).slice(0, 40)}</td>)}</tr>)}
          </tbody></table></div>
          <div className="mt-2 flex gap-2">
            <Btn tone="ghost" disabled={page <= 1} onClick={() => open(sel.name, sel.source, page - 1)}>Previous</Btn>
            <span className="self-center font-mono text-xs">page {page}</span>
            <Btn tone="ghost" disabled={page * 50 >= rows.total} onClick={() => open(sel.name, sel.source, page + 1)}>Next</Btn>
          </div>
        </div>
      )}
    </div>
  );
}

export function WorkbookCoverage({ wb, note }) {
  return (
    <Card title="Workbook-to-app coverage" sub={wb.inspected ? `${wb.file} · audited ${ist(wb.inspected_at)}` : "workbook not inspected"} testid="workbook-coverage">
      <Note tone={wb.inspected ? "slate" : "amber"}>{wb.note}</Note>
      {wb.inspected && <p className="mt-2 font-mono text-[11px] text-slate-400">Hidden: {wb.hidden.join(", ")} · Named ranges: {wb.defined_names.map((d) => `${d[0]}=${d[1]}`).join(", ")} · Remaining unavailable: {wb.unavailable.join("; ")}</p>}
      <div className="mt-3 overflow-x-auto scroll-thin"><table className="desk-table">
        <thead><tr><th>Worksheet</th><th>State</th><th>Range</th><th>Formulas / values</th><th>Main functions</th><th>App feature</th><th>Status</th><th>Findings</th></tr></thead>
        <tbody>{wb.coverage.map((r) => (
          <tr key={r.sheet}><td>{r.sheet}<div className="text-[10px] text-slate-500">{r.backup}</div></td><td className="text-xs">{r.state}</td><td className="font-mono text-[11px]">{r.dims}</td>
            <td className="font-mono text-[11px]">{r.formulas} / {r.values}</td><td className="font-mono text-[10.5px] text-slate-400">{r.functions}</td><td className="text-xs">{r.app_feature}</td>
            <td><Chip status="PASS" label={String(r.status || "").toUpperCase()} /></td><td className="max-w-[280px] text-[11px] text-slate-400">{r.findings}</td></tr>
        ))}</tbody>
      </table></div>
      <p className="mt-2 text-[11px] text-slate-500">{note}</p>
      {wb.inspected && <RefArchive />}
    </Card>
  );
}
