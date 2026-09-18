"use client";

import {
  MarkdownTextPrimitive,
  type CodeHeaderProps,
} from "@assistant-ui/react-markdown";
import { TextMessagePartProvider, useMessagePartText, useSmooth } from "@assistant-ui/react";
import remend from "remend";
import remarkGfm from "remark-gfm";
import { Children, memo, useMemo, useState, type ComponentPropsWithoutRef, type ReactElement, type ReactNode } from "react";
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

// One run's file list can run to hundreds of rows, which buries the closing
// paragraphs. Long lists and tables start folded, the way the main chat clients
// fold long output, and the total stays visible next to the toggle.
const LONG_BLOCK_ITEMS = 12;
const LONG_BLOCK_PREVIEW = 8;

type MarkdownElement = { type?: unknown; props?: { children?: ReactNode } };

/** Markdown arrives with whitespace text nodes between elements, so counting or
 * slicing the raw children would fold the wrong number of items. */
function elementChildren(children: ReactNode): ReactElement[] {
  return Children.toArray(children).filter(
    (child): child is ReactElement => typeof child === "object" && child !== null && "type" in child,
  );
}

function ClampFooter({ total, preview, unit, open, onToggle }: {
  total: number; preview: number; unit: string; open: boolean; onToggle: () => void;
}) {
  return (
    <div className="aui-md-clamp">
      <span>
        {open
          ? `已展开全部 ${total} ${unit}`
          : `共 ${total} ${unit}，另有 ${Math.max(total - preview, 0)} ${unit}未显示`}
      </span>
      <button type="button" onClick={onToggle}>
        {open ? "收起" : `展开全部（${total} ${unit}）`}
      </button>
    </div>
  );
}

function CollapsibleList({ ordered, children, node: _node, ...props }: ComponentPropsWithoutRef<"ul"> & { ordered?: boolean; node?: unknown }) {
  const [open, setOpen] = useState(false);
  const items = elementChildren(children);
  const Tag = ordered ? "ol" : "ul";
  if (items.length <= LONG_BLOCK_ITEMS) return <Tag {...props}>{children}</Tag>;
  return (
    <>
      <Tag {...props}>{open ? children : items.slice(0, LONG_BLOCK_PREVIEW)}</Tag>
      <ClampFooter
        total={items.length}
        preview={LONG_BLOCK_PREVIEW}
        unit="项"
        open={open}
        onToggle={() => setOpen((value) => !value)}
      />
    </>
  );
}

function CollapsibleUnorderedList(props: ComponentPropsWithoutRef<"ul">) {
  return <CollapsibleList {...props} />;
}

function CollapsibleOrderedList(props: ComponentPropsWithoutRef<"ol">) {
  return <CollapsibleList ordered {...props} />;
}

/** Fold a long table body, keeping its header and the row count in view. */
function CollapsibleTable({ children, ...props }: ComponentPropsWithoutRef<"table">) {
  const [open, setOpen] = useState(false);
  const parts = elementChildren(children) as MarkdownElement[];
  const body = parts.find((part) => part?.type === "tbody");
  const rows = body?.props ? elementChildren(body.props.children) : [];
  const folded = rows.length > LONG_BLOCK_ITEMS;
  const table = (
    <ScrollableTable {...props}>
      {folded && !open
        ? (parts.map((part) => part === body
            ? { ...body, props: { ...body.props, children: rows.slice(0, LONG_BLOCK_PREVIEW) } }
            : part) as ReactNode)
        : children}
    </ScrollableTable>
  );
  if (!folded) return table;
  return (
    <>
      {table}
      <ClampFooter
        total={rows.length}
        preview={LONG_BLOCK_PREVIEW}
        unit="行"
        open={open}
        onToggle={() => setOpen((value) => !value)}
      />
    </>
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
// One multi-hundred-KB markdown message can block the main thread for seconds
// (the 2026-09-17 "page unresponsive" reports). Render a prefix first and let
// the user expand; the copy action still copies the complete provider text.
const MESSAGE_TEXT_CLAMP_CHARS = 20_000;
// An agent that ships dozens of files often lists them across many small
// per-section lists, so a per-list limit never triggers. This budget folds the
// file bullets of the whole answer instead, leaving one way back to all of them.
const MESSAGE_FILE_BULLET_LIMIT = 12;
const FILE_BULLET_PATTERN = /^\s{0,3}(?:[-*+]|\d+\.)\s+.*[`/][\w./-]+\.(?:[a-z0-9]{1,5})[\s`（(,，.。]?/i;

export function foldFileBullets(markdown: string, limit = MESSAGE_FILE_BULLET_LIMIT) {
  const kept: string[] = [];
  const hidden: string[] = [];
  let seen = 0;
  for (const line of markdown.split("\n")) {
    if (!FILE_BULLET_PATTERN.test(line)) {
      kept.push(line);
      continue;
    }
    seen += 1;
    if (seen <= limit) kept.push(line);
    else hidden.push(line.trim());
  }
  return { text: kept.join("\n"), hidden };
}
function markdownUrlTransform(url: string) {
  return url === "streamdown:incomplete-link" ? url : knowledgeUrlTransform(url);
}

function MarkdownTextImpl() {
  const part = useMessagePartText();
  const normalized = useMemo(() => ({ ...part, text: normalizeMessageText(part.text) }), [part]);
  const smooth = useSmooth(normalized, part.status.type === "running" ? STREAM_SMOOTHING : FINAL_SMOOTHING);
  const running = smooth.status.type === "running";
  const [expanded, setExpanded] = useState(false);
  // Complete syntax only in the display projection, after smoothing. Stored
  // text and the message copy action retain the exact provider response.
  const displayText = useMemo(
    () => running ? remend(smooth.text, { katex: false }) : smooth.text,
    [running, smooth.text],
  );
  const oversized = !running && !expanded && displayText.length > MESSAGE_TEXT_CLAMP_CHARS;
  const renderedText = oversized
    ? displayText.slice(0, MESSAGE_TEXT_CLAMP_CHARS)
    : displayText;
  const [filesOpen, setFilesOpen] = useState(false);
  const folded = useMemo(
    () => (running || filesOpen ? { text: renderedText, hidden: [] as string[] } : foldFileBullets(renderedText)),
    [running, filesOpen, renderedText],
  );
  return (
    <TextMessagePartProvider text={folded.text} isRunning={running}>
      <MarkdownTextPrimitive
        className="aui-md"
        remarkPlugins={[remarkGfm, remarkWikiLinks]}
        smooth={false}
        defer
        // react-markdown blanks unknown protocols; wiki: must survive so the
        // renderer can turn it into a page-opening button.
        urlTransform={markdownUrlTransform}
        components={{
          CodeHeader,
          a: WikiLink,
          table: CollapsibleTable,
          ul: CollapsibleUnorderedList,
          ol: CollapsibleOrderedList,
        }}
        componentsByLanguage={{
          mermaid: {
            CodeHeader: MermaidCodeHeader,
            SyntaxHighlighter: MermaidDiagram,
          },
        }}
      />
      {folded.hidden.length > 0 ? (
        <div className="aui-md-clamp">
          <span>
            本次产出共 {folded.hidden.length + MESSAGE_FILE_BULLET_LIMIT} 项，另有 {folded.hidden.length} 项未显示
          </span>
          <button type="button" onClick={() => setFilesOpen(true)}>
            展开全部（{folded.hidden.length + MESSAGE_FILE_BULLET_LIMIT} 项）
          </button>
        </div>
      ) : null}
      {oversized ? (
        <div className="aui-md-clamp">
          <span>消息过长，已先显示前 {MESSAGE_TEXT_CLAMP_CHARS} 字符</span>
          <button type="button" onClick={() => setExpanded(true)}>
            展开全部（{displayText.length} 字符）
          </button>
        </div>
      ) : null}
    </TextMessagePartProvider>
  );
}

export const MarkdownText = memo(MarkdownTextImpl);
