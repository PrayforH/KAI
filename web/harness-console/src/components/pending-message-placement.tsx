"use client";
import { useLayoutEffect, useRef, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";

/** An old locally retained send belongs beside its original turn, not the composer. */
export function PendingMessagePlacement({ runId, children }: { runId?: string; children: ReactNode }) {
  const holder = useRef<HTMLDivElement>(null);
  const [container, setContainer] = useState<HTMLDivElement | null>(null);
  useLayoutEffect(() => {
    const frame = holder.current?.closest(".harness-thread-frame");
    const node = document.createElement("div");
    const attach = () => {
      const turn = runId ? [...(frame?.querySelectorAll<HTMLElement>("[data-turn-id]") ?? [])]
        .find(element => element.dataset.turnId?.includes(runId)) : null;
      const target = turn ?? holder.current;
      if (target && node.parentElement !== target) target.append(node);
    };
    attach(); setContainer(node);
    const observer = new MutationObserver(attach);
    if (frame) observer.observe(frame, { childList: true, subtree: true });
    return () => { observer.disconnect(); node.remove(); };
  }, [runId]);
  return <><div ref={holder} />{container && createPortal(children, container)}</>;
}
