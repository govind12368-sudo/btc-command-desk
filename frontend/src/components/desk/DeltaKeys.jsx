import React, { useState } from "react";
import { toast } from "sonner";
import { api, errText } from "@/lib/api";
import { useFetch } from "@/hooks/useFetch";
import { Card, Note } from "@/components/desk/ui";
import { Field, Input, Btn } from "@/components/desk/forms";
import { ist } from "@/lib/fmt";

const INFO = {
  TESTNET: { title: "Testnet key (practice exchange, fake money)", site: "testnet.delta.exchange", step: "Needed first: Live unlocks only after one complete Testnet trade." },
  LIVE: { title: "Live key (your real Delta Exchange India account)", site: "india.delta.exchange", step: "Real money. Give it Read Data and Trading only, never Withdrawal." },
};

function KeyBox({ mode, s, ip, reload, busy, setBusy }) {
  const [key, setKey] = useState("");
  const [secret, setSecret] = useState("");
  const [sure, setSure] = useState(false);
  const save = async () => {
    setBusy(true);
    try {
      await api.put("/autotrade/keys", { mode, key, secret });
      setKey(""); setSecret("");
      toast.success(`${mode === "LIVE" ? "Live" : "Testnet"} key checked with Delta and saved.`);
      reload();
    } catch (e) { toast.error(errText(e)); }
    setBusy(false);
  };
  const remove = async () => {
    setBusy(true);
    try { await api.delete(`/autotrade/keys/${mode}`); toast.success("Key removed from the server."); setSure(false); reload(); } catch (e) { toast.error(errText(e)); }
    setBusy(false);
  };
  const i = INFO[mode];
  return (
    <section className="rounded-lg border border-[#1e2a4a] bg-[#0f172a]/60 p-3" data-testid={`delta-key-${mode}`}>
      <h4 className="text-[13px] font-semibold text-slate-100">{i.title}</h4>
      <p className="mt-1 text-[12.5px]" data-testid={`delta-key-${mode}-status`}>
        {s?.from_env ? <b className="text-teal-300">Set in the server file (.env). It takes priority over a pasted key.</b>
          : s?.saved ? <b className="text-teal-300">Connected · key ending {s.last4} · checked {ist(s.saved_at)}{s.checked_balance_inr != null ? ` · balance seen ₹${Math.round(s.checked_balance_inr).toLocaleString("en-IN")}` : ""}</b>
            : s?.broken ? <b className="text-red-300">The saved key can no longer be read (the server secret changed). Paste it again.</b>
              : <b className="text-amber-300">Not connected</b>}
      </p>
      <p className="mt-1 text-[11.5px] text-slate-400">{i.step}</p>
      <div className="mt-2 grid gap-2 sm:grid-cols-2">
        <Field label="API key"><Input type="password" autoComplete="off" spellCheck={false} value={key} onChange={(e) => setKey(e.target.value)} placeholder="Paste the API key" data-testid={`delta-key-${mode}-key`} /></Field>
        <Field label="API secret"><Input type="password" autoComplete="off" spellCheck={false} value={secret} onChange={(e) => setSecret(e.target.value)} placeholder="Paste the API secret" data-testid={`delta-key-${mode}-secret`} /></Field>
      </div>
      <div className="mt-2 flex flex-wrap gap-2">
        <Btn disabled={busy || !key.trim() || !secret.trim()} onClick={save} data-testid={`delta-key-${mode}-save`}>{busy ? "Checking with Delta…" : s?.saved ? "Check and replace key" : "Check and connect"}</Btn>
        {s?.saved && (sure
          ? <><Btn tone="red" disabled={busy} onClick={remove}>Yes, remove it</Btn><Btn tone="ghost" onClick={() => setSure(false)}>Keep it</Btn></>
          : <Btn tone="ghost" disabled={busy} onClick={() => setSure(true)}>Remove key</Btn>)}
      </div>
      <p className="mt-2 text-[11px] text-slate-500">Create it on {i.site} → Account → API Keys. Whitelist IP {ip || "of this server"}.</p>
    </section>
  );
}

/** Admin pastes the Delta API key here. It is checked with Delta from this server, stored encrypted, and never shown again. */
export default function DeltaKeys() {
  const { data, reload } = useFetch("/autotrade/keys", 0);
  const [busy, setBusy] = useState(false);
  if (!data) return null;
  return (
    <Card title="Delta Exchange connection" testid="delta-keys" sub="Paste each key once. It is checked with Delta from this server, stored encrypted, and never shown again. It cannot be changed while the bot is trading in that mode.">
      <ol className="mb-3 list-decimal space-y-0.5 pl-5 text-[12.5px] text-slate-300">
        <li>On Delta, create an API key with <b>Read Data</b> and <b>Trading</b> permission. Leave <b>Withdrawal off</b>.</li>
        <li>Whitelist this server's IP address: <b className="font-mono text-slate-50" data-testid="delta-keys-ip">{data.server_ip || "not known"}</b>. Without it Delta refuses every order.</li>
        <li>Paste the key and secret below and press Check and connect.</li>
      </ol>
      <div className="grid gap-3 lg:grid-cols-2">
        {["TESTNET", "LIVE"].map((m) => <KeyBox key={m} mode={m} s={data[m]} ip={data.server_ip} reload={reload} busy={busy} setBusy={setBusy} />)}
      </div>
      <div className="mt-3"><Note>Connecting a key does not start trading. The bot stays in its current mode until you switch it in Controls.</Note></div>
    </Card>
  );
}
