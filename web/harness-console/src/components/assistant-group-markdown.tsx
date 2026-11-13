"use client";

import { memo, type ComponentPropsWithoutRef, type ReactNode } from "react";
import ReactMarkdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";

import { remarkMentions } from "../lib/remark-mentions";
import styles from "./assistant-group-markdown.module.css";

function Code({ children, className, ...props }: ComponentPropsWithoutRef<"code">) {
  const block = Boolean(className?.includes("language-"));
  return (
    <code className={block ? `${styles.codeBlock} ${className}` : styles.codeInline} {...props}>
      {children}
    </code>
  );
}

function Pre({ children }: { children?: ReactNode }) {
  return <pre className={styles.pre}>{children}</pre>;
}

function Table({ children }: { children?: ReactNode }) {
  return (
    <div className={styles.tableScroll} role="region" aria-label="表格，可横向滚动" tabIndex={0}>
      <table className={styles.table}>{children}</table>
    </div>
  );
}

const COMPONENTS: Components = {
  code: Code,
  pre: Pre,
  table: Table,
  a: ({ href, children }) => (
    <a
      className={styles.link}
      href={href}
      target="_blank"
      rel="noreferrer noopener"
    >
      {children}
    </a>
  ),
  blockquote: ({ children }) => <blockquote className={styles.quote}>{children}</blockquote>,
  hr: () => <hr className={styles.rule} />,
  img: ({ src, alt }) => (
    <img className={styles.image} src={typeof src === "string" ? src : undefined} alt={alt ?? ""} loading="lazy" />
  ),
  h1: ({ children }) => <h1 className={styles.heading}>{children}</h1>,
  h2: ({ children }) => <h2 className={styles.heading}>{children}</h2>,
  h3: ({ children }) => <h3 className={styles.heading}>{children}</h3>,
  h4: ({ children }) => <h4 className={styles.heading}>{children}</h4>,
  h5: ({ children }) => <h5 className={styles.heading}>{children}</h5>,
  h6: ({ children }) => <h6 className={styles.heading}>{children}</h6>,
};

/**
 * 群消息的 Markdown 渲染：标题、列表、表格、代码块、引用都能显示，
 * @某人 由 remark 插件转成芯片。纯文本仍然按原来的换行显示。
 */
export const AssistantGroupMarkdown = memo(function AssistantGroupMarkdown({
  text,
}: {
  text: string;
}) {
  return (
    <div className={styles.markdown}>
      <ReactMarkdown remarkPlugins={[remarkGfm, remarkMentions]} components={COMPONENTS}>
        {text}
      </ReactMarkdown>
    </div>
  );
});
