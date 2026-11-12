import { useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api";

export function useFetch(url, interval = 0) {
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const load = useCallback(() => api.get(url).then((r) => { setData(r.data); setError(null); }).catch((e) => setError(e.message)), [url]);
  useEffect(() => {
    load();
    if (!interval) return undefined;
    const i = setInterval(() => { if (!document.hidden) load(); }, interval);
    return () => clearInterval(i);
  }, [load, interval]);
  return { data, error, reload: load };
}
