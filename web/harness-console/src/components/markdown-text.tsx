"use client";

import {
  MarkdownTextPrimitive,
  type CodeHeaderProps,
} from "@assistant-ui/react-markdown";
import { TextMessagePartProvider, useMessagePartText } from "@assistant-ui/react";
import remarkGfm from "remark-gfm";
import { memo, useState, type ComponentPropsWithoutRef } from "react";
import { normalizeMessageText } from "../lib/message-text";
import { MermaidCodeHeader, MermaidDiagram } from "./mermaid-diagram";
import { citationTarget, knowledgeUrlTransform, remarkWikiLinks } from "../lib/knowledge-links";
import { useAnswerCitations } from "./knowledge/answer-citation-context";
import { CitationLink } from "./knowledge/citation-link";
import { WikiEntityLink } from "./knowledge/wiki-entity-link";
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

function WikiLink({
  href,
  children,
  node: _node,
  ...props
}: ComponentPropsWithoutRef<"a"> & { node?: unknown }) {
  const answer = useAnswerCitations();
  if (href?.startsWith("citation:")) {
    const citation = answer?.citations.find((item) => citationTarget(item) === href);
    return citation ? <CitationLink className="aui-citation-link" title={citation.title ?? "查看来源"} aria-label={`查看来源 ${citation.index}：${citation.title ?? "文档"}`} onClick={() => answer?.request(citation)}>{children}</CitationLink> : <span title="引用来源暂不可用">{children}</span>;
  }
  if (typeof href === "string" && href.startsWith("wiki:")) {
    let slug: string;
    try { slug = decodeURIComponent(href.slice("wiki:".length)); } catch { return <span>{children}</span>; }
    return (
      <WikiEntityLink
        className="aui-wiki-link"
        onClick={() => {
          window.dispatchEvent(
            new CustomEvent("harness:open-wiki", { detail: { slug } }),
          );
        }}
      >
        {children}
      </WikiEntityLink>
    );
  }
  return (
    <SourceLink href={href} {...props}>
      {children}
    </SourceLink>
  );
}

const MESSAGE_TEXT_CLAMP_CHARS = 20_000;

function MarkdownTextImpl() {
  const part = useMessagePartText();
  const [expanded, setExpanded] = useState(false);
  const running = part.status.type === "running";
  const oversized = !running && !expanded && part.text.length > MESSAGE_TEXT_CLAMP_CHARS;
  const renderedText = oversized
    ? part.text.slice(0, MESSAGE_TEXT_CLAMP_CHARS)
    : part.text;
  return (
    <TextMessagePartProvider text={renderedText} isRunning={running}>
      <MarkdownTextPrimitive
        className="aui-md"
        remarkPlugins={[remarkGfm, remarkWikiLinks]}
        preprocess={normalizeMessageText}
        // The live response store already batches network deltas per animation
        // frame. A second character-by-character reveal exposes incomplete
        // Markdown delimiters (for example `**`) until their closing token is
        // replayed, which looks like a final-pass renderer. Parse every received
        // delta immediately so Markdown remains formatted throughout streaming.
        smooth={false}
        // react-markdown blanks unknown protocols; wiki: must survive so the
        // renderer can turn it into a page-opening button.
        urlTransform={knowledgeUrlTransform}
        components={{ CodeHeader, a: WikiLink, table: ScrollableTable }}
        componentsByLanguage={{
          mermaid: {
            CodeHeader: MermaidCodeHeader,
            SyntaxHighlighter: MermaidDiagram,
          },
        }}
      />
      {oversized ? (
        <div className="aui-md-clamp">
          <span>消息过长，已先显示前 {MESSAGE_TEXT_CLAMP_CHARS} 字符</span>
          <button type="button" onClick={() => setExpanded(true)}>
            展开全部（{part.text.length} 字符）
          </button>
        </div>
      ) : null}
    </TextMessagePartProvider>
  );
}

export const MarkdownText = memo(MarkdownTextImpl);
