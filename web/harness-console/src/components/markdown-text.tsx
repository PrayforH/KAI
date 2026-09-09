"use client";

import {
  MarkdownTextPrimitive,
  type CodeHeaderProps,
} from "@assistant-ui/react-markdown";
import remarkGfm from "remark-gfm";
import { memo, useState, type ComponentPropsWithoutRef } from "react";
import { normalizeMessageText } from "../lib/message-text";
import { MermaidCodeHeader, MermaidDiagram } from "./mermaid-diagram";
import { SourceLink } from "./source-link";

function CodeHeader({ language, code }: CodeHeaderProps) {
  const [copied, setCopied] = useState(false);

  async function copy() {
    await navigator.clipboard?.writeText(code);
    setCopied(true);
    window.setTimeout(() => setCopied(false), 1400);
  }

  return (
    <div className="aui-code-header">
      <span>{language || "text"}</span>
      <button type="button" onClick={copy} aria-label="复制代码">
        {copied ? "已复制" : "复制"}
      </button>
    </div>
  );
}

function ScrollableTable(props: ComponentPropsWithoutRef<"table">) {
  return (
    <div
      className="aui-table-scroll"
      role="region"
      aria-label="表格，可横向滚动"
      tabIndex={0}
    >
      <table {...props} />
    </div>
  );
}

const WIKILINK = /\[\[([^\]|]+)(?:\|([^\]]+))?\]\]/g;

/** Turn WeKnora wiki links into markdown links the renderer can intercept. */
function preprocessMessage(text: string): string {
  return normalizeMessageText(text).replace(
    WIKILINK,
    (_match, slug: string, label?: string) =>
      `[${(label ?? slug).trim()}](wiki:${encodeURIComponent(slug.trim())})`,
  );
}

function WikiLink({
  href,
  children,
  node: _node,
  ...props
}: ComponentPropsWithoutRef<"a"> & { node?: unknown }) {
  if (typeof href === "string" && href.startsWith("wiki:")) {
    const slug = decodeURIComponent(href.slice("wiki:".length));
    return (
      <button
        type="button"
        className="aui-wiki-link"
        onClick={() => {
          window.dispatchEvent(
            new CustomEvent("harness:open-wiki", { detail: { slug } }),
          );
        }}
      >
        {children}
      </button>
    );
  }
  return (
    <SourceLink href={href} {...props}>
      {children}
    </SourceLink>
  );
}

function MarkdownTextImpl() {
  return (
    <MarkdownTextPrimitive
      className="aui-md"
      remarkPlugins={[remarkGfm]}
      preprocess={preprocessMessage}
      // The live response store already batches network deltas per animation
      // frame. A second character-by-character reveal exposes incomplete
      // Markdown delimiters (for example `**`) until their closing token is
      // replayed, which looks like a final-pass renderer. Parse every received
      // delta immediately so Markdown remains formatted throughout streaming.
      smooth={false}
      // react-markdown blanks unknown protocols; wiki: must survive so the
      // renderer can turn it into a page-opening button.
      urlTransform={(url: string) => url}
      components={{ CodeHeader, a: WikiLink, table: ScrollableTable }}
      componentsByLanguage={{
        mermaid: {
          CodeHeader: MermaidCodeHeader,
          SyntaxHighlighter: MermaidDiagram,
        },
      }}
    />
  );
}

export const MarkdownText = memo(MarkdownTextImpl);
