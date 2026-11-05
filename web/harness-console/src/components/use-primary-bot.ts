"use client";
import { useEffect, useState } from "react";
import { assistantRequest, type Specialist } from "../lib/assistant-client";
import { peekClientRead, rememberClientRead } from "../lib/client-read-cache";

/** Main contact reuses the Bot profile while retaining its original conversation. */
export function usePrimaryBot(userId: string, tenantId?: string) {
  const key = `primary-bot:${tenantId ?? ""}:${userId}`;
  const [snapshot, setSnapshot] = useState<{key: string; bot: Specialist | null}>(() => ({key, bot: peekClientRead<Specialist>(key) ?? null}));
  useEffect(() => {
    let active = true;
    let generation = 0;
    const load = () => { const request = ++generation; void assistantRequest<Specialist | null>("primary").then(bot => {
      if (!active || request !== generation) return;
      if (bot) rememberClientRead(key, bot);
      setSnapshot({key, bot});
    }).catch(() => { /* Keep the original main conversation usable offline. */ }); };
    load();
    window.addEventListener("kai:specialists-changed", load);
    return () => { active = false; window.removeEventListener("kai:specialists-changed", load); };
  }, [key]);
  return snapshot.key === key ? snapshot.bot : peekClientRead<Specialist>(key) ?? null;
}
