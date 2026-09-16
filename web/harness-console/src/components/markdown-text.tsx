"use client";

import {
  MarkdownTextPrimitive,
  type CodeHeaderProps,
} from "@assistant-ui/react-markdown";
import { TextMessagePartProvider, useMessagePartText, useSmooth } from "@assistant-ui/react";
import remend from "remend";
import remarkGfm from "remark-gfm";
import { memo, useMemo, useState, type ComponentPropsWithoutRef } from "react";
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
  if (href === "streamdown:incomplete-link") return <span>{children}</span>;
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

const STREAM_SMOOTHING = { drainMs: 120, maxCharIntervalMs: 4, minCommitMs: 32 };
const FINAL_SMOOTHING = { drainMs: 32, maxCharIntervalMs: 1, minCommitMs: 32 };
function markdownUrlTransform(url: string) {
  return url === "streamdown:incomplete-link" ? url : knowledgeUrlTransform(url);
}

function MarkdownTextImpl() {
  const part = useMessagePartText();
  const normalized = useMemo(() => ({ ...part, text: normalizeMessageText(part.text) }), [part]);
  const smooth = useSmooth(normalized, part.status.type === "running" ? STREAM_SMOOTHING : FINAL_SMOOTHING);
  const running = smooth.status.type === "running";
  // Complete syntax only in the display projection, after smoothing. Stored
  // text and the message copy action retain the exact provider response.
  const displayText = useMemo(
    () => running ? remend(smooth.text, { katex: false }) : smooth.text,
    [running, smooth.text],
  );
  return (
    <TextMessagePartProvider text={displayText} isRunning={running}>
      <MarkdownTextPrimitive
        className="aui-md"
        remarkPlugins={[remarkGfm, remarkWikiLinks]}
        smooth={false}
        defer
        // react-markdown blanks unknown protocols; wiki: must survive so the
        // renderer can turn it into a page-opening button.
        urlTransform={markdownUrlTransform}
        components={{ CodeHeader, a: WikiLink, table: ScrollableTable }}
        componentsByLanguage={{
          mermaid: {
            CodeHeader: MermaidCodeHeader,
            SyntaxHighlighter: MermaidDiagram,
          },
        }}
      />
    </TextMessagePartProvider>
  );
}

export const MarkdownText = memo(MarkdownTextImpl);
