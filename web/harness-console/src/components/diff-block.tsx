"use client";
import { CardActionButton } from "./card-action-button";

import { useState } from "react";
import { truncateLines } from "../lib/content-format";

interface DiffBlockProps {
  diff: string;
  maxLines?: number;
}

function diffLineClass(line: string): string {
  if (line.startsWith("@@")) return "diff-line-meta";
  if (line.startsWith("+") && !line.startsWith("+++")) return "diff-line-add";
  if (line.startsWith("-") && !line.startsWith("---")) return "diff-line-remove";
  if (line.startsWith("diff ") || line.startsWith("index ")) return "diff-line-header";
  return "diff-line-context";
}

export function DiffBlock({ diff, maxLines = 500 }: DiffBlockProps) {
  const [copied, setCopied] = useState(false);
  const visible = truncateLines(diff, maxLines);

  async function copy() {
    await navigator.clipboard?.writeText(diff);
    setCopied(true);
    window.setTimeout(() => setCopied(false), 1400);
  }

  return (
    <section className="code-block diff-block" aria-label="代码变更">
      <header className="code-block-header">
        <span>diff</span>
        <CardActionButton action="copy" label="复制变更" copied={copied} onClick={copy} />
      </header>
      <pre><code>
        {visible.value.split("\n").map((line, index) => (
          <span className={`code-line ${diffLineClass(line)}`} key={`${index}-${line}`}>
            <span className="code-line-number" aria-hidden="true">{index + 1}</span>
            <span className="code-line-content">{line || " "}</span>
          </span>
        ))}
      </code></pre>
      {visible.truncated > 0 && (
        <p className="content-truncated">已隐藏 {visible.truncated} 行</p>
      )}
    </section>
  );
}
