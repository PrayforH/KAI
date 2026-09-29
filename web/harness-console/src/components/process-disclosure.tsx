"use client";

import { useEffect, useLayoutEffect, useRef, type ReactNode } from "react";

/** Animate only disclosure changes, never each arriving token or tool update. */
export function ProcessDisclosure({ open, children }: { open: boolean; children: ReactNode }) {
  const element = useRef<HTMLDivElement>(null);
  const initialOpen = useRef(open);
  const initialized = useRef(false);
  const animation = useRef<Animation | null>(null);
  useLayoutEffect(() => {
    const node = element.current;
    if (!node) return;
    const from = node.getBoundingClientRect().height;
    animation.current?.cancel();
    const animate = initialized.current && typeof node.animate === "function" &&
      !window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    initialized.current = true;
    node.hidden = false;
    node.style.height = "auto";
    const to = open ? node.getBoundingClientRect().height : 0;
    node.style.height = open ? "auto" : "0px";
    if (!animate || from === to) {
      node.hidden = !open;
      return;
    }
    const current = node.animate(
      [{ height: `${from}px`, opacity: open ? 0 : 1 }, { height: `${to}px`, opacity: open ? 1 : 0 }],
      { duration: 180, easing: "cubic-bezier(.2,.7,.2,1)" },
    );
    animation.current = current;
    current.onfinish = () => { node.hidden = !open; animation.current = null; };
  }, [open]);
  useEffect(() => () => animation.current?.cancel(), []);
  return <div ref={element} className="execution-process-reveal" hidden={!initialOpen.current}
    aria-hidden={!open} inert={!open}>{children}</div>;
}
