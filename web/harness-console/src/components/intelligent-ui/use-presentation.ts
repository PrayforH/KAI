"use client";

import { useEffect, useRef, useState } from "react";
import {
  terminalExecution,
  uiJson,
  uiPath,
  type UiExecution,
  type UiSnapshot,
  type UiViewState,
} from "./client";
import type { Presentation } from "./schema";

/** One snapshot per block, serial state saves, and durable action recovery. */
export function usePresentation(
  p: Presentation,
  connected: boolean,
  onSettled?: (runId: string) => void,
) {
  const [view, setView] = useState<UiViewState>({});
  const [requests, setRequests] = useState<UiExecution[]>([]);
  const [loaded, setLoaded] = useState(!connected);
  const [error, setError] = useState("");
  const stateVersion = useRef(0);
  const saveQueue = useRef<UiViewState | null>(null);
  const saving = useRef(false);
  const conflict = useRef(false);
  const alive = useRef(true);
  const notified = useRef(new Set<string>());
  const dirty = useRef(false);
  const settled = useRef(onSettled);
  settled.current = onSettled;
  const base = uiPath(p.presentation_id);

  useEffect(() => {
    alive.current = true;
    if (!connected)
      return () => {
        alive.current = false;
      };
    let cancelled = false;
    void uiJson<UiSnapshot>(`${base}?revision=${p.revision}`)
      .then((snapshot) => {
        if (cancelled) return;
        stateVersion.current = snapshot.view_state.version;
        const submitted = snapshot.action_requests.find(
          (r) =>
            r.revision === p.revision &&
            snapshot.actions.some(
              (a) => a.action_id === r.action_id && a.kind === "write",
            ),
        );
        const restored =
          submitted?.submitted_values && p.kind === "form"
            ? { values: submitted.submitted_values }
            : snapshot.view_state.state;
        if (!dirty.current) setView(restored);
        else void drain();
        setRequests(
          snapshot.action_requests.filter((r) => r.revision === p.revision),
        );
        setLoaded(true);
      })
      .catch((cause) => {
        if (!cancelled) setError(cause.message);
      });
    return () => {
      cancelled = true;
      alive.current = false;
    };
  }, [base, connected, p.revision]);

  const activeIds = requests
    .filter((r) => !terminalExecution(r.execution_status))
    .map((r) => r.request_id)
    .join(",");
  useEffect(() => {
    if (!connected || !activeIds) return;
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout>;
    const poll = async () => {
      try {
        const updated = await Promise.all(
          activeIds
            .split(",")
            .map((id) =>
              uiJson<UiExecution>(`${base}/actions/${encodeURIComponent(id)}`),
            ),
        );
        if (cancelled) return;
        setRequests((current) =>
          current.map(
            (r) =>
              updated.find((next) => next.request_id === r.request_id) ?? r,
          ),
        );
        if (!conflict.current) setError("");
        for (const request of updated) {
          const notice = `${request.request_id}:${request.execution_status}`;
          if (
            request.run_id &&
            (terminalExecution(request.execution_status) ||
              request.execution_status === "waiting_approval") &&
            !notified.current.has(notice)
          ) {
            notified.current.add(notice);
            settled.current?.(request.run_id);
          }
        }
      } catch (cause) {
        if (!cancelled)
          setError(
            cause instanceof Error ? cause.message : "状态读取失败，将继续重试",
          );
      }
      if (!cancelled) timer = setTimeout(() => void poll(), 1500);
    };
    timer = setTimeout(() => void poll(), 500);
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [activeIds, base, connected]);

  async function drain() {
    if (saving.current || conflict.current) return;
    saving.current = true;
    try {
      while (saveQueue.current && alive.current) {
        const state = saveQueue.current;
        saveQueue.current = null;
        const saved = await uiJson<{ version: number }>(`${base}/view-state`, {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            revision: p.revision,
            expected_version: stateVersion.current,
            state,
          }),
        });
        stateVersion.current = saved.version;
      }
    } catch (cause) {
      conflict.current = true;
      if (alive.current)
        setError(
          cause instanceof Error
            ? cause.message
            : "状态保存失败，输入仍保留在此页",
        );
    } finally {
      saving.current = false;
    }
  }

  const saveTimer = useRef<ReturnType<typeof setTimeout> | undefined>(
    undefined,
  );
  useEffect(() => () => clearTimeout(saveTimer.current), []);
  function changeView(state: UiViewState) {
    dirty.current = true;
    setView(state);
    if (!connected) return;
    saveQueue.current = state;
    if (!loaded) return;
    clearTimeout(saveTimer.current);
    saveTimer.current = setTimeout(() => void drain(), 350);
  }
  function accept(request: UiExecution | void) {
    if (!request) return;
    setRequests((current) => [
      ...current.filter((r) => r.request_id !== request.request_id),
      request,
    ]);
    if (request.run_id && terminalExecution(request.execution_status))
      settled.current?.(request.run_id);
  }
  return { view, changeView, requests, accept, loaded, error };
}
