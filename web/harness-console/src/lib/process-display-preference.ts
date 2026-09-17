"use client";

import { useSyncExternalStore } from "react";

const KEY = "agent-studio:detailed-process:v1";
const EVENT = "agent-studio:process-display-change";
let fallback = false;

function getSnapshot() {
  try { return window.localStorage.getItem(KEY) === "detailed"; }
  catch { return fallback; }
}
function subscribe(listener: () => void) {
  const storage = (event: StorageEvent) => {
    if (event.key === KEY || event.key === null) listener();
  };
  window.addEventListener(EVENT, listener);
  window.addEventListener("storage", storage);
  return () => {
    window.removeEventListener(EVENT, listener);
    window.removeEventListener("storage", storage);
  };
}
export function useDetailedProcess() {
  return useSyncExternalStore(subscribe, getSnapshot, () => false);
}
export function setDetailedProcess(value: boolean) {
  fallback = value;
  try { window.localStorage.setItem(KEY, value ? "detailed" : "compact"); }
  catch { /* Keep the preference for this page when storage is unavailable. */ }
  window.dispatchEvent(new Event(EVENT));
}
