"use client";

import { useEffect, useRef } from "react";
import { waitForProjectTaskTransition } from "../lib/project-task-transition";
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
    void waitForProjectTaskTransition(scope.threadId).then(() => {
      if (active) void tryProjectTaskLaunch(scope.userId, scope.threadId, () => sendRef.current());
    });
    return () => { active = false; };
  }, [scope, ready]);
}
