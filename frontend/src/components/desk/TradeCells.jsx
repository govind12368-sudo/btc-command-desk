import React from "react";
import { n, signedUsd } from "@/lib/fmt";

const RES = { TARGET: "border-teal-500/40 bg-teal-500/10 text-teal-300", STOP: "border-red-500/40 bg-red-500/10 text-red-300",
  TIME: "border-amber-500/40 bg-amber-500/10 text-amber-300", OPEN: "border-sky-500/40 bg-sky-500/10 text-sky-300" };
const RES_NAME = { TARGET: "Target", STOP: "Stop", TIME: "6h exit", OPEN: "Open" };
export const DIR_TONE = { LONG: "text-teal-300", SHORT: "text-red-300" };
const t = (s) => (s ? s.slice(5, 16).replace("-", "/") : "");   // "2026-11-14 08:15" → "11/14 08:15"

/** Signal ID with its capture time (IST) underneath. */
export function SignalCell({ id, captured, extra }) {
  return (
    <td className="whitespace-nowrap">
      <div className="font-mono text-[11.5px] text-slate-200">{id}</div>
      <div className="font-mono text-[10.5px] text-slate-500">{captured} IST{extra ? ` · ${extra}` : ""}</div>
    </td>
  );
}

/** Result badge with the exit time (IST) underneath. */
export function ResultCell({ result, exit }) {
  if (!result) return <td className="text-slate-500">—</td>;
  return (
    <td className="whitespace-nowrap">
      <span className={`inline-flex rounded border px-2 py-0.5 text-[11px] font-semibold ${RES[result] || "border-slate-600 text-slate-300"}`}>{RES_NAME[result] || result}</span>
      {exit && <div className="mt-0.5 font-mono text-[10.5px] text-slate-500">{t(exit)}</div>}
    </td>
  );
}

export function FillCell({ status, time }) {
  if (status === "FILLED") return <td className="whitespace-nowrap font-mono text-[11.5px] text-slate-200">{t(time) || "filled"}</td>;
  return <td className="whitespace-nowrap text-[11.5px] text-slate-500">{status === "NOT FILLED" ? "Not filled" : status === "WAITING" ? "Waiting" : status || "—"}</td>;
}

export function NetCell({ v }) {
  return <td className={`num font-mono ${v == null ? "text-slate-500" : v >= 0 ? "text-teal-300" : "text-red-300"}`}>{v == null ? "—" : signedUsd(v)}</td>;
}

export function PriceCell({ v, sub }) {
  return <td className="num font-mono">{v == null ? "—" : n(v, 1)}{sub && <div className="text-[10px] text-slate-500">{sub}</div>}</td>;
}

/** Why the trade won or lost, plus any warning that applies to the signal itself. */
export function WhyCell({ why, context }) {
  return (
    <td className="why">
      {why || "—"}
      {context && <div className="mt-1 text-[11px] text-amber-300/90">{context}</div>}
    </td>
  );
}
