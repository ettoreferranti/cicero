import { useEffect, useState } from "react";
import { eventsUrl } from "./api";
import type { ConsensusResult, Turn } from "./types";

export interface DebateStreamState {
  status: string | null;
  liveTurns: Turn[];
  consensus: ConsensusResult | null;
  done: boolean;
  error: string | null;
}

const INITIAL: DebateStreamState = {
  status: null,
  liveTurns: [],
  consensus: null,
  done: false,
  error: null,
};

/**
 * Subscribe to a chamber's debate event stream while `enabled`. Accumulates
 * turns as they arrive and captures the final consensus. Content is rendered
 * as text by React (auto-escaped), so untrusted model output is XSS-safe.
 */
export function useDebateStream(
  chamberId: string | null,
  enabled: boolean,
): DebateStreamState {
  const [state, setState] = useState<DebateStreamState>(INITIAL);

  useEffect(() => {
    if (!chamberId || !enabled) return;
    if (typeof EventSource === "undefined") return; // e.g. in unit tests
    setState(INITIAL);
    const source = new EventSource(eventsUrl(chamberId));

    const onTurn = (event: MessageEvent) => {
      const data = JSON.parse(event.data) as { payload: { turn: Turn } };
      setState((prev) => ({ ...prev, liveTurns: [...prev.liveTurns, data.payload.turn] }));
    };
    const onStatus = (event: MessageEvent) => {
      const data = JSON.parse(event.data) as { payload: { status: string } };
      setState((prev) => ({ ...prev, status: data.payload.status }));
    };
    const onConsensus = (event: MessageEvent) => {
      const data = JSON.parse(event.data) as { payload: { consensus: ConsensusResult } };
      setState((prev) => ({ ...prev, consensus: data.payload.consensus }));
    };
    const onError = (event: MessageEvent) => {
      const data = JSON.parse(event.data) as { payload: { message: string } };
      setState((prev) => ({ ...prev, error: data.payload.message }));
    };
    const onDone = () => {
      setState((prev) => ({ ...prev, done: true }));
      source.close();
    };

    source.addEventListener("turn", onTurn);
    source.addEventListener("status", onStatus);
    source.addEventListener("consensus", onConsensus);
    source.addEventListener("error", onError as EventListener);
    source.addEventListener("done", onDone);

    return () => source.close();
  }, [chamberId, enabled]);

  return state;
}
