import React from "react";
import { Link } from "react-router-dom";
import { CheckCircle2, Circle, XCircle } from "lucide-react";
import { n, usd, ist } from "@/lib/fmt";

// What each check protects you from, in plain words (shown when a check is not passing).
export const CHECK_PLAIN = {
  F26: "Prices and candles must be fresh (not delayed).",
  F27: "Candle data must be complete and clean.",
  F28: "Short and higher timeframe trends must point the same way.",
  F29: "The latest completed candle must confirm the move.",
  F30: "Bid/ask spread must be tight enough to enter.",
  F32: "Your risk and cost settings must be valid.",
  F33: "Daily loss, entry and position limits must allow a new trade.",
  F34: "The Delta BTCUSD contract must be live and valid.",
  F35: "Profit after fees must be worth the risk.",
  F36: "Price must not have run too far — no chasing.",
  F37: "Order size and stop/target levels must be consistent.",
};

const CADENCE = { scalp: "re-checked at every completed 5-minute candle", swing: "re-checked at every completed 1-hour candle" };

function pick(scalp, swing) {
  const rank = (e) => (e?.status === "BUY" || e?.status === "SELL" ? 100 : 0) + (e?.passed || 0) + (e?.levels ? 0.5 : 0);
  return [scalp, swing].filter(Boolean).sort((a, b) => rank(b) - rank(a))[0];
}

export function NextSetup({ scalp, swing, isAdmin }) {
  const ev = pick(scalp, swing);
  if (!ev) return null;
  const total = ev.total_checks ?? ev.checks?.length ?? 11;
  const fails = (ev.checks || []).filter((c) => c.status !== "PASS");
  const lv = ev.levels;
  const dir = lv?.direction || ev.direction;
  const live = ev.status === "BUY" || ev.status === "SELL";
  const name = ev.strategy === "scalp" ? "Scalping" : "Swing";
  const head = live ? `${name} ${dir} — all checks pass` : fails.length === 1 ? `${name} ${dir || ""} — 1 check away` : `${name} ${dir || ""} — ${fails.length} checks away`;
  return (
    <section className="desk-card p-5" data-testid="next-setup">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <div className="text-[13px] font-medium text-slate-400">Closest trade setup</div>
          <h3 className={`mt-0.5 text-xl font-bold tracking-tight ${live ? (dir === "LONG" ? "text-teal-300" : "text-red-300") : "text-slate-50"}`} data-testid="next-setup-title">{head.replace(/\s+—/, " —")}</h3>
          <div className="text-[12px] text-slate-500">{ev.passed}/{total} checks pass · {CADENCE[ev.strategy]}</div>
        </div>
        <Link to={ev.strategy === "scalp" ? "/scalping" : "/swing"} className="rounded-md border border-[#1e2a4a] px-3 py-1.5 text-xs text-slate-200 hover:border-amber-500/50">Full {name.toLowerCase()} checklist</Link>
      </div>

      <div className="mt-4 grid gap-5 lg:grid-cols-[1.1fr_1fr]">
        <div>
          <div className="mb-2 text-[13px] font-semibold text-slate-200">{live ? "Why it qualifies" : "What still has to happen"}</div>
          {live ? <p className="text-[13px] text-slate-300">Every active check passes on the latest completed candle. Verify on Delta before placing any order — this desk never trades for you.</p> : (
            <ul className="space-y-2.5">
              {fails.map((c) => (
                <li key={c.id} className="rounded-lg border border-[#1e2a4a] bg-[#0f172a]/60 p-3">
                  <div className="flex items-center gap-2 text-[13px] text-slate-100"><XCircle size={15} className="shrink-0 text-red-300" /><span className="font-mono text-[11px] text-slate-500">{c.id}</span>{c.name}</div>
                  <p className="ml-6 mt-0.5 text-[12px] text-slate-400">{CHECK_PLAIN[c.id] || c.detail}</p>
                  {c.conditions?.length > 0 && (
                    <ul className="ml-6 mt-1.5 space-y-1">
                      {c.conditions.map((x, i) => (
                        <li key={i} className="flex items-start gap-1.5 text-[12.5px]">
                          {x.ok ? <CheckCircle2 size={14} className="mt-0.5 shrink-0 text-teal-300" /> : <Circle size={14} className="mt-0.5 shrink-0 text-amber-300" />}
                          <span className={x.ok ? "text-slate-400" : "text-slate-100"}>{x.plain}</span>
                        </li>
                      ))}
                    </ul>
                  )}
                </li>
              ))}
              {!fails.length && <li className="text-[13px] text-slate-400">Waiting for the next completed candle.</li>}
            </ul>
          )}
        </div>
        <div>
          <div className="mb-2 text-[13px] font-semibold text-slate-200">{live ? "Planned order" : "If it triggers, the plan would be"}</div>
          {!lv ? <p className="text-[13px] text-slate-500">No levels yet — needs a clear direction and valid data.</p> : (
            <div className="rounded-lg border border-[#1e2a4a] bg-[#0f172a]/60 p-3 font-mono text-[13px]" data-testid="next-setup-ticket">
              <Row k="Side" v={<span className={dir === "LONG" ? "text-teal-300" : "text-red-300"}>{dir === "LONG" ? "▲ LONG (buy)" : "▼ SHORT (sell)"}</span>} />
              <Row k="Entry" v={`${n(lv.entry, 1)} · ${String(lv.entry_type || "MARKET").toLowerCase()}`} />
              <Row k="Stop loss" v={<span className="text-red-300">{n(lv.stop, 1)}</span>} />
              <Row k="Target" v={<span className="text-teal-300">{n(lv.target, 1)}</span>} />
              <Row k="Size" v={`${n(lv.qty_btc, 3)} BTC · ${lv.contracts ?? "—"} contracts`} />
              {isAdmin && lv.risk_usd != null && <Row k="Risk → reward" v={`${usd(lv.risk_usd, 0)} → ${usd(lv.reward_usd, 0)}`} />}
              <Row k="Reward / risk after costs" v={`${n(lv.net_r, 2)} R`} />
              {lv.valid_until && <Row k="Limit valid until" v={ist(lv.valid_until)} />}
            </div>
          )}
          <p className="mt-2 text-[11.5px] text-slate-500">Model plan only. Levels update every candle until the setup qualifies.</p>
        </div>
      </div>
    </section>
  );
}

function Row({ k, v }) {
  return <div className="flex items-baseline justify-between gap-3 border-b border-[#1e2a4a]/60 py-1 last:border-0"><span className="font-sans text-[12px] text-slate-400">{k}</span><span className="text-right text-slate-100">{v}</span></div>;
}
