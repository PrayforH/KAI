"use client";
import { useEffect, useRef } from "react";
import type { QueuedPrompt } from "../lib/composer-interactions";
import { readSteeringState } from "../lib/steering-poller";

/** Recover accepted legacy sends even when their history page is not loaded.
 * One shared read per original Run, after history; never resend or poll. */
export function useRetainedPromptRecovery({enabled, scopeKey, items, onAccepted}: {
  enabled: boolean; scopeKey: string | null; items: readonly QueuedPrompt[];
  onAccepted: (ids: ReadonlySet<string>) => void;
}) {
  const callback = useRef(onAccepted);
  callback.current = onAccepted;
  const key = JSON.stringify(items.filter(item => item.receipt && item.historyRunId)
    .map(item => [item.historyRunId!, item.id]));
  useEffect(() => {
    if (!enabled || !scopeKey || key === "[]") return;
    const pending = JSON.parse(key) as Array<[string, string]>;
    const runs = [...new Set(pending.map(([run]) => run))];
    let active = true;
    void Promise.allSettled(runs.map(async run => ({run, state: await readSteeringState(run)})))
      .then(results => {
        if (!active) return;
        const accepted = new Set<string>();
        for (const result of results) {
          if (result.status !== "fulfilled") continue;
          for (const request of result.value.state.requests) {
            if (request.status === "accepted" && pending.some(([run,id]) =>
              run === result.value.run && id === request.request_id)) accepted.add(request.request_id);
          }
        }
        if (accepted.size) callback.current(accepted);
      });
    return () => { active = false; };
  }, [enabled, scopeKey, key]);
}
