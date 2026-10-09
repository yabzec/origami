import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "@/lib/api";
import { openAgentUrl } from "@/lib/agentLaunch";
import {
  initialSearch,
  isAgentInstalled,
  markAgentInstalled,
  searchTick,
  startSearch,
  type SearchState,
} from "@/lib/localScan";

const PREFETCH_MAX_AGE_MS = 90_000; // launch tokens live 120 s on the server

export function useLocalScanSearch(clientId: string, input: { agentConnected: boolean; localCount: number }) {
  const [search, setSearch] = useState<SearchState>(initialSearch);
  const launchUrl = useRef<{ url: string; at: number } | null>(null);

  const inFlight = useRef(false);

  const fetchUrl = useCallback(async () => {
    const { url } = await api.post<{ url: string }>("/api/agent/launch", { client_id: clientId });
    launchUrl.current = { url, at: Date.now() };
    return url;
  }, [clientId]);

  const warm = useCallback(() => {
    const cached = launchUrl.current;
    if (inFlight.current || (cached && Date.now() - cached.at < PREFETCH_MAX_AGE_MS)) return;
    inFlight.current = true;
    fetchUrl()
      .catch(() => {})
      .finally(() => {
        inFlight.current = false;
      });
  }, [fetchUrl]);

  const prefetch = useCallback(() => {
    if (isAgentInstalled()) warm();
  }, [warm]);

  const launch = useCallback(() => {
    const cached = launchUrl.current;
    launchUrl.current = null; // single use
    setSearch(startSearch(true, Date.now()));
    if (cached && Date.now() - cached.at < PREFETCH_MAX_AGE_MS) {
      openAgentUrl(cached.url); // synchronous: keeps the click's user activation
      warm(); // next click gets a fresh one
      return;
    }
    fetchUrl()
      .then((url) => {
        launchUrl.current = null;
        openAgentUrl(url);
      })
      .catch(() => setSearch({ phase: "unresponsive", startedAt: Date.now(), found: 0 }));
  }, [fetchUrl, warm]);

  const start = useCallback(() => {
    if (input.agentConnected) {
      setSearch(startSearch(true, Date.now())); // agent already running: browse now and poll again
      api.post("/api/agent/discover", { client_id: clientId }).catch(() => {});
      return;
    }
    if (!isAgentInstalled()) {
      setSearch(startSearch(false, Date.now()));
      return;
    }
    launch();
  }, [clientId, input.agentConnected, launch]);

  const confirmInstalled = launch;

  useEffect(() => {
    if (input.agentConnected) markAgentInstalled();
  }, [input.agentConnected]);

  // The install and retry buttons need a ready URL to open synchronously.
  useEffect(() => {
    if (search.phase === "install" || search.phase === "unresponsive") warm();
  }, [search.phase, warm]);

  useEffect(() => {
    // unresponsive too: an agent that connects late resumes the search
    if (search.phase !== "searching" && search.phase !== "unresponsive") return;
    const id = setInterval(() => setSearch((s) => searchTick(s, { now: Date.now(), ...input })), 500);
    setSearch((s) => searchTick(s, { now: Date.now(), ...input }));
    return () => clearInterval(id);
  }, [search.phase, input.agentConnected, input.localCount]); // eslint-disable-line react-hooks/exhaustive-deps

  return { search, start, confirmInstalled, prefetch };
}
