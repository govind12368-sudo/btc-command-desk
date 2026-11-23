import { useSyncExternalStore } from "react";

// Live push store (outside React state so a pulse every second does not re-render whole pages).
// Components that show the live price subscribe with usePulse(); data hooks listen for their topic marker with onTopic().
let current = null;
let ok = false;
const listeners = new Set();
const emit = () => listeners.forEach((f) => f());

export const live = {
  get: () => current,
  ok: () => ok,
  set(p) { current = p; emit(); },
  setOk(v) { if (v !== ok) { ok = v; emit(); } },
  subscribe(f) { listeners.add(f); return () => listeners.delete(f); },
};

export const usePulse = () => useSyncExternalStore(live.subscribe, live.get);
export const useStreamOk = () => useSyncExternalStore(live.subscribe, live.ok);

/** Call cb when the server's marker for `topic` changes (not on the first marker seen). Returns an unsubscribe function. */
export function onTopic(topic, cb) {
  let last = current?.v?.[topic];
  return live.subscribe(() => {
    const v = current?.v?.[topic];
    if (v === undefined || v === last) return;
    const prev = last;
    last = v;
    if (prev !== undefined) cb();
  });
}

// API path -> push topic
const TOPICS = [["/state", "state"], ["/candle-runs", "runs"], ["/delta-top3", "trio"], ["/elite", "elite"], ["/option-picks", "optpicks"], ["/flush-short", "flush"],
  ["/plan-a", "plan_a"], ["/regime-journal", "regime"], ["/observer", "observer"], ["/autotrade", "autotrade"], ["/signals", "signals"], ["/news", "news"], ["/desk-summary", "state"],
  ["/switch-support", "state"], ["/market", "state"], ["/scanner", "state"]];
export const topicFor = (url) => (TOPICS.find(([p]) => url.startsWith(p)) || [])[1];
