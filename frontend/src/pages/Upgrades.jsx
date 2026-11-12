import React, { useState } from "react";
import { toast } from "sonner";
import { useDesk } from "@/context/DeskContext";
import { useFetch } from "@/hooks/useFetch";
import { api, errText } from "@/lib/api";
import { Card, Chip, PageHead, Empty, Note } from "@/components/desk/ui";
import { Field, Input, Area, Btn } from "@/components/desk/forms";
import { ist } from "@/lib/fmt";

const BLANK = { title: "", prompt: "", expected: "" };

export default function Upgrades() {
  const { isAdmin } = useDesk();
  const { data, reload } = useFetch("/upgrades");
  const [f, setF] = useState(BLANK);
  const [edit, setEdit] = useState(null);
  const [err, setErr] = useState(null);
  const max = data?.max_chars || 6000;
  const save = async () => {
    setErr(null);
    try {
      if (edit) await api.put(`/upgrades/${edit.id}`, { ...f, done: edit.done, version: edit.version }); else await api.post("/upgrades", f);
      toast.success("Saved privately"); setF(BLANK); setEdit(null); reload();
    } catch (e) { setErr(errText(e)); }
  };
  const toggle = async (x) => { try { await api.put(`/upgrades/${x.id}`, { title: x.title, prompt: x.prompt, expected: x.expected, done: !x.done, version: x.version }); reload(); } catch (e) { toast.error(errText(e)); } };
  const del = async (x) => { if (!window.confirm("Delete this request?")) return; try { await api.delete(`/upgrades/${x.id}`); reload(); } catch (e) { toast.error(errText(e)); } };
  const prepared = async (x) => (await api.get(`/upgrades/${x.id}/prepared`, { responseType: "text" })).data;
  const copy = async (x) => {
    try { const t = await prepared(x); await navigator.clipboard.writeText(t); toast.success("Prepared request copied with current diagnostics"); } catch (e) { toast.error(`Copy failed: ${errText(e)}`); }
  };
  const download = async (x) => {
    const t = await prepared(x);
    const url = URL.createObjectURL(new Blob([t], { type: "text/plain" }));
    const a = document.createElement("a"); a.href = url; a.download = `upgrade-request-${(x.title || x.id).replace(/\W+/g, "-").slice(0, 40)}.txt`; a.click(); URL.revokeObjectURL(url);
  };
  return (
    <div className="space-y-4">
      <PageHead title="Upgrade Requests" sub="Write down a change or fault. Saving stores it privately on this desk. To get it implemented, copy the prepared request and paste it into your builder/assistant chat — saved text never changes the application by itself." />
      {isAdmin ? (
        <Card title={edit ? "Edit request" : "New request"} sub={`${data?.items?.length ?? 0}/${data?.limit ?? 20} saved · up to ${max} characters`} testid="upgrade-form">
          <div className="grid gap-3">
            <Field label="Title (optional)"><Input data-testid="upgrade-title" value={f.title} onChange={(e) => setF({ ...f, title: e.target.value })} /></Field>
            <Field label="Describe the change or fault" hint={`${f.prompt.length}/${max} characters`}><Area data-testid="upgrade-prompt" maxLength={max} value={f.prompt} onChange={(e) => setF({ ...f, prompt: e.target.value })} className="min-h-[140px]" /></Field>
            <Field label="Expected improvement (optional)"><Area data-testid="upgrade-expected" value={f.expected} onChange={(e) => setF({ ...f, expected: e.target.value })} /></Field>
          </div>
          {err && <div className="mt-2"><Note tone="red">Not saved: {err}. Your draft is kept.</Note></div>}
          <div className="mt-3 flex gap-2"><Btn data-testid="upgrade-save" disabled={!f.prompt.trim()} onClick={save}>{edit ? "Save changes" : "Save request"}</Btn>{edit && <Btn tone="ghost" onClick={() => { setEdit(null); setF(BLANK); }}>Cancel edit</Btn>}</div>
        </Card>
      ) : <Note>View-only account: you can read, copy and download requests.</Note>}
      {!data ? <Empty>Loading…</Empty> : data.items.length === 0 ? <Empty>No saved requests.</Empty> : data.items.map((x) => (
        <Card key={x.id} title={x.title || "(untitled)"} sub={`saved ${ist(x.created_at)} · updated ${ist(x.updated_at)}`} testid={`upgrade-${x.id}`} right={<Chip status={x.done ? "PASS" : "WAIT"} label={x.done ? "DONE" : "OPEN"} />}>
          <p className="whitespace-pre-wrap break-words [overflow-wrap:anywhere] text-sm text-slate-300">{x.prompt}</p>
          {x.expected && <p className="mt-2 break-words [overflow-wrap:anywhere] text-xs text-slate-400">Expected: {x.expected}</p>}
          <div className="mt-3 flex flex-wrap gap-2">
            <Btn tone="teal" data-testid={`upgrade-copy-${x.id}`} onClick={() => copy(x)}>Copy prepared request</Btn>
            <Btn tone="ghost" data-testid={`upgrade-download-${x.id}`} onClick={() => download(x)}>Download .txt</Btn>
            {isAdmin && <><Btn tone="ghost" data-testid={`upgrade-edit-${x.id}`} onClick={() => { setEdit(x); setF({ title: x.title || "", prompt: x.prompt, expected: x.expected || "" }); window.scrollTo({ top: 0, behavior: "smooth" }); }}>Edit</Btn>
              <Btn tone="ghost" data-testid={`upgrade-toggle-${x.id}`} onClick={() => toggle(x)}>{x.done ? "Reopen" : "Mark done"}</Btn>
              <Btn tone="red" data-testid={`upgrade-delete-${x.id}`} onClick={() => del(x)}>Delete</Btn></>}
          </div>
        </Card>
      ))}
    </div>
  );
}
