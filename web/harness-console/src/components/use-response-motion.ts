"use client";

import { useLayoutEffect, type RefObject } from "react";

const blocks = ".aui-md > :is(p,h1,h2,h3,h4,blockquote,ul,ol,pre,.aui-code-header,.aui-table-scroll), .aui-md li, .aui-md tbody tr, .aui-md img";

/** Reveal insertions only. Updating a text node never restarts its animation. */
export function useResponseMotion(ref: RefObject<HTMLElement | null>, hasContent: boolean) {
  useLayoutEffect(() => {
    const root = ref.current;
    if (!root || !hasContent) return;
    const reduced = window.matchMedia?.("(prefers-reduced-motion: reduce)");
    const seen = new WeakSet<Element>();
    const animations = new Set<Animation>();
    let interactionUntil = 0;
    // Existing history is immediately readable, including after reconnects.
    root.querySelectorAll(blocks).forEach(element => seen.add(element));
    const revealInteraction = (event: Event) => {
      if (event.target instanceof Element && event.target.closest(".aui-md-clamp button")) {
        interactionUntil = performance.now() + 500;
      }
    };
    const cancel = () => {
      animations.forEach(animation => animation.cancel());
      animations.clear();
    };
    const onPreferenceChange = () => { if (reduced?.matches) cancel(); };
    const observer = new MutationObserver(records => {
      const candidates = new Set<Element>();
      for (const record of records) for (const node of record.addedNodes) {
        if (!(node instanceof Element) || !root.contains(node)) continue;
        if (node.matches(blocks)) candidates.add(node);
        node.querySelectorAll(blocks).forEach(element => candidates.add(element));
      }
      const selection = document.getSelection();
      const readingSelection = selection && !selection.isCollapsed
        && root.contains(selection.anchorNode);
      const animate = !reduced?.matches && !readingSelection
        && (root.dataset.streaming === "true" || performance.now() < interactionUntil);
      for (const element of candidates) {
        if (seen.has(element)) continue;
        seen.add(element);
        // Animate the inserted parent once, rather than every nested child.
        let parent = element.parentElement;
        while (parent && parent !== root && !candidates.has(parent)) parent = parent.parentElement;
        if (!animate || (parent && candidates.has(parent)) || typeof element.animate !== "function") continue;
        const animation = element.animate([{ opacity: 0 }, { opacity: 1 }], {
          duration: 140, easing: "cubic-bezier(.37,.55,.86,.88)",
        });
        animations.add(animation);
        animation.onfinish = animation.oncancel = () => animations.delete(animation);
      }
    });
    observer.observe(root, { childList: true, subtree: true });
    root.addEventListener("click", revealInteraction, true);
    reduced?.addEventListener?.("change", onPreferenceChange);
    return () => {
      observer.disconnect();
      root.removeEventListener("click", revealInteraction, true);
      reduced?.removeEventListener?.("change", onPreferenceChange);
      cancel();
    };
  }, [ref, hasContent]);
}
