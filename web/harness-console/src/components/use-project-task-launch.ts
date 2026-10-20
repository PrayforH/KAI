"use client";

import { useEffect, useRef } from "react";
import { revealProjectTaskTransition } from "../lib/project-task-transition";
import { tryProjectTaskLaunch } from "../lib/project-task-launch";

/** A launch must survive loading/remounts until the shared composer accepts it. */
export function useProjectTaskLaunch(
  scope: { userId: string; threadId: string } | null,
  ready: boolean,
  send: () => Promise<boolean>,
) {
  const sendRef = useRef(send);
  sendRef.current = send;
  useEffect(() => {
    if (!scope || !ready) return;
    let active = true;
    queueMicrotask(() => {
      if (!active) return;
      // Start execution as soon as the runtime is ready; animation never gates a run.
      void tryProjectTaskLaunch(scope.userId, scope.threadId, () => sendRef.current());
      revealProjectTaskTransition(scope.threadId);
    });
    return () => { active = false; };
  }, [scope, ready]);
}
