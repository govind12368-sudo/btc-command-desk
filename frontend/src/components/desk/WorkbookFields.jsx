import React from "react";
import { useFetch } from "@/hooks/useFetch";
import { Card, KV, Expand } from "./ui";

const fmtField = (k, v) => {
  if (v === null || v === undefined) return "—";
  if (typeof v === "number" && /date/i.test(k) && v > 30000 && v < 80000) return new Date(Date.UTC(1899, 11, 30) + v * 86400000).toISOString().slice(0, 10);
  return String(v).slice(0, 40);
};

export function WorkbookPillarFields() {
  const { data } = useFetch("/audit");
  const f = data?.workbook?.five_pillar_fields || [];
  if (!f.length) return null;
  return (
    <Card title={`Original workbook Five Pillar fields (${f.length})`} sub="Historical snapshot from the attached workbook — reference only, not live data" testid="workbook-pillar-fields">
      <Expand label="Show all original fields and their saved values" testid="toggle-pillar-fields">
        <div className="grid gap-x-6 sm:grid-cols-2 lg:grid-cols-3">{f.map((x, i) => <KV key={i} k={x.field} v={fmtField(x.field, x.value)} />)}</div>
      </Expand>
    </Card>
  );
}
