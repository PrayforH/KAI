"use client";

import { UserMessage } from "@assistant-ui/react-ui";
import { useEffect, useId, useRef, useState } from "react";

import { CodexResponseIcon } from "./codex-response-icon";

/** Folding belongs to submitted messages; the composer always remains editable. */
export function SentUserContent() {
  const contentId = useId();
  const content = useRef<HTMLDivElement>(null);
  const [long, setLong] = useState(false);
  const [expanded, setExpanded] = useState(false);
  useEffect(() => {
    const node = content.current;
    if (!node) return;
    const measure = () => setLong(node.scrollHeight > parseFloat(getComputedStyle(node).fontSize) * 12 + 2);
    measure();
    const resize = new ResizeObserver(measure);
    resize.observe(node);
    return () => resize.disconnect();
  }, []);
  return <>
    <UserMessage.Content id={contentId} ref={content} className="sent-user-content" data-collapsed={long && !expanded} />
    {long && <button type="button" className="sent-user-expand" aria-expanded={expanded} aria-controls={contentId}
      aria-label={expanded ? "收起全文" : "展开全文"} title={expanded ? "收起全文" : "展开全文"}
      onClick={() => setExpanded(value => !value)}><CodexResponseIcon name="chevronDown" /></button>}
  </>;
}
