"use client";
import { useEffect, useState } from "react";
import { useAuth } from "../components/auth-provider";
export function useInternalAgentsPreference() {
  const { user } = useAuth();
  const key = `harness:preferences:${user.user_id}:internal-agents`;
  const [enabled, setEnabled] = useState(false);
  useEffect(() => {
    const sync = () => { try { setEnabled(localStorage.getItem(key) === "true"); } catch { setEnabled(false); } };
    sync();
    window.addEventListener("storage", sync);
    window.addEventListener("harness-preferences", sync);
    return () => { window.removeEventListener("storage", sync); window.removeEventListener("harness-preferences", sync); };
  }, [key]);
  return [enabled, (value: boolean) => {
    setEnabled(value);
    try { localStorage.setItem(key, String(value)); } catch { /* Session preference remains usable. */ }
    window.dispatchEvent(new Event("harness-preferences"));
  }] as const;
}

export type FollowUpBehavior = "queue" | "steer";
export function useFollowUpPreference() {
  const { user } = useAuth();
  const key = `harness:preferences:${user.user_id}:follow-up`;
  const [behavior, setBehavior] = useState<FollowUpBehavior>("queue");
  useEffect(() => {
    const sync = () => {
      try { setBehavior(localStorage.getItem(key) === "steer" ? "steer" : "queue"); }
      catch { setBehavior("queue"); }
    };
    sync();
    window.addEventListener("storage", sync);
    window.addEventListener("harness-preferences", sync);
    return () => { window.removeEventListener("storage", sync); window.removeEventListener("harness-preferences", sync); };
  }, [key]);
  return [behavior, (value: FollowUpBehavior) => {
    setBehavior(value);
    try { localStorage.setItem(key, value); } catch { /* Keep the session preference. */ }
    window.dispatchEvent(new Event("harness-preferences"));
  }] as const;
}
