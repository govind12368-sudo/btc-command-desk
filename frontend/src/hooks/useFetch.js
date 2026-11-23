import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "@/lib/api";
import { live, onTopic, topicFor, useStreamOk } from "@/lib/live";

/** Load `url` and keep it fresh.
 *  With the live push stream up, the page reloads as soon as the server marks its topic changed, and polls only as a slow
 *  safety net (5x the interval, at least every 60 s). Without the stream it polls every `interval` ms as before. */
export function useFetch(url, interval = 0, opts = {}) {
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const busy = useRef(false);
  const streamOk = useStreamOk();
  const topic = opts.topic === undefined ? topicFor(url) : opts.topic;
  const fetchNow = useCallback(async () => {
    try { const r = await api.get(url); setData(r.data); setError(null); } catch (e) { setError(e.message); }
  }, [url]);
  const load = useCallback(async () => {          // background refresh: never two at once
    if (busy.current) return;
    busy.current = true;
    try { await fetchNow(); } finally { busy.current = false; }
  }, [fetchNow]);
  useEffect(() => { load(); }, [load]);
  useEffect(() => {
    if (!interval) return undefined;
    const every = streamOk && topic ? Math.max(60000, interval * 5) : interval;
    const i = setInterval(() => { if (!document.hidden) load(); }, every);
    return () => clearInterval(i);
  }, [load, interval, streamOk, topic]);
  useEffect(() => (topic && interval ? onTopic(topic, () => { if (!document.hidden) load(); }) : undefined), [topic, load, interval]);
  return { data, error, reload: fetchNow, live: streamOk && !!topic && !!live.get() };
}
