import React, { useEffect, useState } from "react";
import { toast } from "sonner";
import { useDesk } from "@/context/DeskContext";
import { api, errText } from "@/lib/api";
import { Card, PageHead, Empty, Note } from "@/components/desk/ui";
import { Field, Input, Select, Btn } from "@/components/desk/forms";
import { AlertsCard, DailySummaryCard } from "@/components/desk/Alerts";
import { PushCard } from "@/components/desk/Push";

const GROUPS = [
  ["Capital & risk", [["capital", "Capital (USD)"], ["risk_fraction", "Risk per trade (fraction of capital)"], ["max_order_notional", "Max order notional (USD)"], ["leverage_cap", "Leverage cap (×)"], ["daily_loss_limit", "Daily realized net-loss limit (USD)"], ["max_entries_per_day", "Max entries per IST day"], ["max_positions", "Max simultaneous positions"], ["journal_capacity", "Journal working capacity (open records)"]]],
  ["Costs & execution", [["fee_per_side", "Legacy flat fee per side for swing/planner (fraction, 0.00059 = 0.05% + GST)"], ["slippage_bps", "Slippage per side (bps)"], ["funding_bps", "Full-hold funding allowance (bps)"], ["max_spread_bps", "Max spread (bps)"], ["max_quote_age_sec", "Max retrieval/quote age (seconds)"]]],
  ["Strategies", [["scalp_stop", "Scalp stop distance (fraction)"], ["scalp_target_r", "Scalp gross target (R)"], ["swing_stop", "Swing stop distance (fraction)"], ["swing_target_r", "Swing gross target (R)"], ["min_scalp_volume_ratio", "Min scalp volume ratio"], ["swing_volume_ratio", "Min swing volume ratio"], ["close_extreme_atr", "Close-to-extreme tolerance (×ATR)"], ["min_candidate_checks", "Min candidate check count (of 12)"]]],
  ["Execution & fees (scalp limit plan)", [["fee_maker_per_side", "Maker fee per side incl. GST (0.000236 = 0.02% + 18%)"], ["fee_taker_per_side", "Taker fee per side incl. GST (0.00059 = 0.05% + 18%)"], ["scalp_entry_mode", "Scalp entry: 1 = post-only limit, 0 = market"], ["scalp_limit_offset_atr", "Limit price offset from close (×ATR, 0 = at close)"], ["scalp_limit_wait_bars", "Cancel limit if not filled after N completed 5m bars"], ["scalp_target_exit_limit", "Take-profit as limit order: 1 = yes, 0 = market"]]],
  ["Scalp V2 filters", [["scalp_min_net_r", "Scalp min net R after costs (F35)"], ["swing_min_net_r", "Swing min net R after costs (F35)"], ["scalp_confirm_signals", "Earlier same-direction setups required (0 = off)"], ["scalp_confirm_lookback_min", "Confirmation look-back (minutes)"], ["scalp_max_ext_atr", "Max close distance beyond EMA20 (×ATR, F36)"], ["max_open_model_signals", "Open model signals per strategy (0 = unlimited)"]]],
  ["Data pipeline", [["macro_score_max_age_h", "Macro series older than this scores neutral (h)"], ["snapshot_min_score", "Min |score| to record a validation snapshot"]]],
  ["News & events", [["critical_news_window_min", "Critical news block window (min)"], ["high_news_window_min", "High-impact review window (min)"], ["event_before_min", "Event block before (min)"], ["event_after_min", "Event block after (min)"]]],
  ["Source ages & macro", [["trades_book_age_min", "Trades/book max age (min)"], ["oi_funding_age_min", "OI/funding max age (min)"], ["tech_candle_age_min", "Technical candle max age (min)"], ["strict_macro_age_days", "Strict macro observation age (days)"], ["macro_meta_max_age_min", "Macro retrieval recency (min)"], ["usd_cap_h", "Broad USD age cap (h, max 264)"], ["y2_cap_h", "US 2Y age cap (h, max 120)"], ["brent_cap_h", "Brent age cap (h, max 240)"]]],
  ["Planner & requests", [["planner_hold_hours", "Planned hold (hours)"], ["planner_win_rate", "Assumed win rate"], ["upgrade_limit", "Max saved upgrade requests"], ["upgrade_chars", "Max prompt characters"]]],
];
const DRAFT = "vantage-settings-draft";

export default function SettingsPage() {
  const { isAdmin } = useDesk();
  const [srv, setSrv] = useState(null);
  const [draft, setDraft] = useState(null);
  const [err, setErr] = useState(null);
  const [saving, setSaving] = useState(false);
  const load = async () => {
    const { data } = await api.get("/settings");
    setSrv(data);
    const saved = localStorage.getItem(DRAFT);
    setDraft(saved ? { ...data.values, ...JSON.parse(saved) } : data.values);
  };
  useEffect(() => { load().catch((e) => setErr(errText(e))); }, []);
  const set = (k, v) => { const d = { ...draft, [k]: v }; setDraft(d); localStorage.setItem(DRAFT, JSON.stringify(d)); };
  const save = async () => {
    setSaving(true); setErr(null);
    try {
      const { data } = await api.put("/settings", { version: srv.version, values: draft });
      localStorage.removeItem(DRAFT);
      setSrv({ ...srv, values: data.values, version: data.version }); setDraft(data.values);
      toast.success(`Saved (version ${data.version})`);
    } catch (e) { setErr(errText(e)); } finally { setSaving(false); }
  };
  const reloadServer = async () => { const keep = draft; const { data } = await api.get("/settings"); setSrv(data); setDraft({ ...data.values, ...keep }); setErr(null); toast("Server version reloaded; your unsaved edits are kept on top."); };
  if (!draft) return <Empty>{err || "Loading settings…"}</Empty>;
  const dirty = JSON.stringify(draft) !== JSON.stringify(srv.values);
  return (
    <div className="space-y-4">
      <PageHead title="Settings" sub={`Editable risk and model controls · server version ${srv.version}. Saved only after the server confirms persistence.`}
        right={isAdmin && <div className="flex gap-2"><Btn tone="ghost" data-testid="settings-defaults" onClick={() => { setDraft({ ...srv.defaults }); localStorage.setItem(DRAFT, JSON.stringify(srv.defaults)); }}>Load defaults (unsaved)</Btn><Btn data-testid="settings-save" disabled={!dirty || saving} onClick={save}>{saving ? "Saving…" : dirty ? "Save changes" : "Saved"}</Btn></div>} />
      {!isAdmin && <Note>View-only account: settings are visible but cannot be changed.</Note>}
      {err && <Note tone="red">Not saved: {err} <button className="underline ml-2" data-testid="settings-reload" onClick={reloadServer}>Reload server version and keep my edits</button></Note>}
      {dirty && !err && <Note tone="amber">Unsaved draft (kept locally until saved).</Note>}
      {isAdmin && <PushCard />}
      <AlertsCard isAdmin={isAdmin} />
      <DailySummaryCard isAdmin={isAdmin} />
      <Card title="New account registration" testid="settings-registration-mode">
        <div className="max-w-sm"><Select data-testid="setting-registration_mode" disabled={!isAdmin} value={draft.registration_mode || "approval"} onChange={(e) => set("registration_mode", e.target.value)} options={[{ value: "approval", label: "Admin approval required (recommended)" }, { value: "open", label: "Open — anyone can sign up and sign in" }, { value: "closed", label: "Closed — no new accounts" }]} /></div>
      </Card>
      <Card title="Macro freshness mode" testid="settings-macro-mode">
        <div className="max-w-sm"><Select data-testid="setting-macro_mode" disabled={!isAdmin} value={draft.macro_mode} onChange={(e) => set("macro_mode", e.target.value)} options={[{ value: "publication", label: "Publication-aware (release schedules)" }, { value: "strict", label: "Strict observation age" }]} /></div>
        <label className="mt-3 flex items-center gap-2 text-sm"><input type="checkbox" data-testid="setting-conviction_scaling" disabled={!isAdmin} checked={!!draft.conviction_scaling} onChange={(e) => set("conviction_scaling", e.target.checked)} /> Planner conviction scaling</label>
      </Card>
      <div className="grid gap-4 lg:grid-cols-2">
        {GROUPS.map(([g, fields]) => (
          <Card key={g} title={g}>
            <div className="grid gap-3 sm:grid-cols-2">
              {fields.map(([k, label]) => <Field key={k} label={label} hint={String(srv.defaults[k]) !== String(draft[k]) ? `default ${srv.defaults[k]}` : null}><Input data-testid={`setting-${k}`} type="number" step="any" disabled={!isAdmin} value={draft[k]} onChange={(e) => set(k, e.target.value)} /></Field>)}
            </div>
          </Card>
        ))}
        <Card title="Planner tranches">
          <div className="grid grid-cols-3 gap-2">{[0, 1, 2].map((i) => <Field key={i} label={`Tranche ${i + 1} fraction`}><Input data-testid={`setting-tranche-${i}`} type="number" step="any" disabled={!isAdmin} value={draft.tranche_fractions[i]} onChange={(e) => { const a = [...draft.tranche_fractions]; a[i] = e.target.value; set("tranche_fractions", a); }} /></Field>)}
            {[0, 1, 2].map((i) => <Field key={`o${i}`} label={`ATR offset ${i + 1}`}><Input data-testid={`setting-offset-${i}`} type="number" step="any" disabled={!isAdmin} value={draft.atr_offsets[i]} onChange={(e) => { const a = [...draft.atr_offsets]; a[i] = e.target.value; set("atr_offsets", a); }} /></Field>)}</div>
        </Card>
      </div>
    </div>
  );
}
