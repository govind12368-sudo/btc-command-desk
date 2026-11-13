import React, { useCallback, useEffect, useState } from "react";
import { toast } from "sonner";
import { api, errText } from "@/lib/api";
import { Card } from "@/components/desk/ui";
import { Btn } from "@/components/desk/forms";
import { ist } from "@/lib/fmt";

const keyBytes = (b64) => {
  const s = atob((b64 + "=".repeat((4 - (b64.length % 4)) % 4)).replace(/-/g, "+").replace(/_/g, "/"));
  return Uint8Array.from(s, (c) => c.charCodeAt(0));
};
const isIOS = () => /iPad|iPhone|iPod/.test(navigator.userAgent) || (navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1);
const isStandalone = () => window.matchMedia?.("(display-mode: standalone)").matches || window.navigator.standalone === true;
const supported = () => "serviceWorker" in navigator && "PushManager" in window && "Notification" in window;

/** Turn phone pop-up notifications on or off for THIS device. */
export function PushCard() {
  const [sub, setSub] = useState(null);
  const [perm, setPerm] = useState(typeof Notification !== "undefined" ? Notification.permission : "default");
  const [devices, setDevices] = useState([]);
  const [busy, setBusy] = useState(false);

  const refresh = useCallback(async () => {
    try {
      if (supported()) {
        const reg = await navigator.serviceWorker.ready;
        setSub(await reg.pushManager.getSubscription());
        setPerm(Notification.permission);
      }
      const { data } = await api.get("/push/status");
      setDevices(data.devices || []);
    } catch (_) { /* status is informative only */ }
  }, []);
  useEffect(() => { refresh(); }, [refresh]);

  const turnOn = async () => {
    setBusy(true);
    try {
      const p = await Notification.requestPermission(); // must be the first step after the tap (iPhone rule)
      setPerm(p);
      if (p !== "granted") { toast.error("Notifications were not allowed. Turn them on in iPhone Settings → Notifications → Vantage BTC."); return; }
      const reg = await navigator.serviceWorker.ready;
      const { data } = await api.get("/push/key");
      const s = (await reg.pushManager.getSubscription()) || (await reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: keyBytes(data.public_key) }));
      await api.post("/push/subscribe", s.toJSON());
      toast.success("Notifications are on for this device. Sending a test…");
      await api.post("/push/test");
      await refresh();
    } catch (e) { toast.error(errText(e) || String(e)); } finally { setBusy(false); }
  };
  const test = async () => {
    setBusy(true);
    try { const { data } = await api.post("/push/test"); toast.success(`Test sent to ${data.sent} device(s)`); await refresh(); } catch (e) { toast.error(errText(e)); } finally { setBusy(false); }
  };
  const turnOff = async () => {
    setBusy(true);
    try {
      if (sub) { await api.post("/push/unsubscribe", { endpoint: sub.endpoint }); await sub.unsubscribe(); }
      toast.success("Notifications are off for this device");
      await refresh();
    } catch (e) { toast.error(errText(e)); } finally { setBusy(false); }
  };

  let body;
  if (isIOS() && !isStandalone()) {
    body = <p className="text-[13px] text-slate-300">On iPhone, notifications only work in the installed app. In Safari tap <b>Share → Add to Home Screen</b>, open <b>Vantage BTC</b> from the home screen, sign in and come back to this card.</p>;
  } else if (!supported()) {
    body = <p className="text-[13px] text-slate-300">This browser can't show notifications. On iPhone you need iOS 16.4 or newer and the home-screen app.</p>;
  } else if (perm === "denied") {
    body = <p className="text-[13px] text-slate-300">Notifications are blocked for this app. Turn them on in <b>iPhone Settings → Notifications → Vantage BTC</b>, then reopen the app.</p>;
  } else if (sub) {
    body = (
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-semibold text-teal-300" data-testid="push-on">On for this device</span>
        <Btn tone="ghost" disabled={busy} onClick={test} data-testid="push-test">Send test</Btn>
        <Btn tone="ghost" disabled={busy} onClick={turnOff} data-testid="push-off">Turn off on this device</Btn>
      </div>
    );
  } else {
    body = <Btn disabled={busy} onClick={turnOn} data-testid="push-on-btn">Turn on notifications on this device</Btn>;
  }
  return (
    <Card title="Phone notifications" testid="push-card"
      sub="Pop-ups for trade entered, exit (target, stop, 6-hour), daily limit hit, errors and new signals. Sent by the server, so they arrive with the app closed and your PC off. They follow the alert switch: off means paper-trade and signal pop-ups stop too.">
      {body}
      {devices.length > 0 && (
        <ul className="mt-3 space-y-1 text-[11.5px] text-slate-400">
          {devices.map((d, i) => (
            <li key={i}>{d.mine ? "Your device" : "Device"}: {(d.device || "unknown").replace(/\(KHTML.*$/, "").slice(0, 70)} · since {ist(d.since)}{d.last_ok ? ` · last delivered ${ist(d.last_ok)}` : ""}{d.last_error ? ` · last problem: ${d.last_error}` : ""}</li>
          ))}
        </ul>
      )}
    </Card>
  );
}
