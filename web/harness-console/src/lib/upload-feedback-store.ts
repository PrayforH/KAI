"use client";

import { useSyncExternalStore } from "react";

export type UploadFeedback = {
  key: string;
  fileName: string;
  status: "uploading" | "ready" | "error";
  message?: string;
  progress?: number;
};

const EMPTY_UPLOAD_FEEDBACK: readonly UploadFeedback[] = [];
let snapshot: readonly UploadFeedback[] = EMPTY_UPLOAD_FEEDBACK;
const listeners = new Set<() => void>();

function publish(next: readonly UploadFeedback[]) {
  snapshot = next;
  for (const listener of listeners) listener();
}

function replace(item: UploadFeedback) {
  const index = snapshot.findIndex((entry) => entry.key === item.key);
  if (index === -1) {
    publish([...snapshot, item]);
    return;
  }
  publish([...snapshot.slice(0, index), item, ...snapshot.slice(index + 1)]);
}

export function uploadKey(file: Pick<File, "name" | "size" | "lastModified">) {
  return `${file.name}:${file.size}:${file.lastModified}`;
}

export const uploadFeedbackStore = {
  begin(key: string, fileName: string) {
    replace({ key, fileName, status: "uploading" });
  },
  progress(key: string, percent: number) {
    const current = snapshot.find((entry) => entry.key === key);
    if (current?.status === "uploading") replace({ ...current, progress: Math.min(100, Math.max(0, Math.round(percent))) });
  },
  succeed(key: string) {
    const current = snapshot.find((entry) => entry.key === key);
    if (!current) return;
    replace({ key, fileName: current.fileName, status: "ready" });
  },
  fail(key: string, message: string) {
    const current = snapshot.find((entry) => entry.key === key);
    if (!current) return;
    replace({ key, fileName: current.fileName, status: "error", message });
  },
  dismiss(key: string) {
    const next = snapshot.filter((entry) => entry.key !== key);
    if (next.length !== snapshot.length) publish(next);
  },
  clear() {
    if (snapshot.length > 0) publish([]);
  },
  subscribe(listener: () => void) {
    listeners.add(listener);
    return () => listeners.delete(listener);
  },
  getSnapshot() {
    return snapshot;
  },
  getServerSnapshot() {
    return EMPTY_UPLOAD_FEEDBACK;
  },
};

export function useUploadFeedback() {
  return useSyncExternalStore(
    uploadFeedbackStore.subscribe,
    uploadFeedbackStore.getSnapshot,
    uploadFeedbackStore.getServerSnapshot,
  );
}
