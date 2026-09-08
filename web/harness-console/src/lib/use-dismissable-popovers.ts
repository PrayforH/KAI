"use client";

import { useEffect } from "react";

const DISMISSABLE_SELECTOR = "details[data-dismiss-on-outside][open]";

export function useDismissablePopovers() {
  useEffect(() => {
    function closePopover(details: HTMLDetailsElement) {
      details.removeAttribute("open");
    }

    function handlePointerDown(event: PointerEvent) {
      const target = event.target;
      if (!(target instanceof Node)) return;
      document
        .querySelectorAll<HTMLDetailsElement>(DISMISSABLE_SELECTOR)
        .forEach((details) => {
          if (!details.contains(target)) closePopover(details);
        });
    }

    function handleKeyDown(event: KeyboardEvent) {
      if (event.key !== "Escape") return;
      document
        .querySelectorAll<HTMLDetailsElement>(DISMISSABLE_SELECTOR)
        .forEach(closePopover);
    }

    document.addEventListener("pointerdown", handlePointerDown);
    document.addEventListener("keydown", handleKeyDown);
    return () => {
      document.removeEventListener("pointerdown", handlePointerDown);
      document.removeEventListener("keydown", handleKeyDown);
    };
  }, []);
}
