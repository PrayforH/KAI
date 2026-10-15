"use client";
import { CardActionButton } from "./card-action-button";

import { useMemo, useState } from "react";
import { classifyContent, toSafeValue } from "../lib/content-format";
import { CodeBlock } from "./code-block";
import { DiffBlock } from "./diff-block";

interface StructuredValueProps {
  value: unknown;
  label?: string;
}

function Primitive({ value }: { value: unknown }) {
  if (value === null) return <span className="json-null">null</span>;
  if (typeof value === "boolean") {
    return <span className="json-boolean">{String(value)}</span>;
  }
  if (typeof value === "number") return <span className="json-number">{value}</span>;
  return <span className="json-string">{JSON.stringify(String(value))}</span>;
}

function JsonTree({ value, depth = 0 }: { value: unknown; depth?: number }) {
  if (value === null || typeof value !== "object") return <Primitive value={value} />;
  const entries = Object.entries(value);
  const array = Array.isArray(value);
  return (
    <details className="json-node" open={depth < 2}>
      <summary>{array ? `[${entries.length}]` : `{${entries.length}}`}</summary>
      <div className="json-children">
        {entries.map(([key, item]) => (
          <div className="json-entry" key={key}>
            <span className="json-key">{array ? key : JSON.stringify(key)}:</span>{" "}
            <JsonTree value={item} depth={depth + 1} />
          </div>
        ))}
      </div>
    </details>
  );
}

export function StructuredValue({ value, label = "JSON" }: StructuredValueProps) {
  const classified = useMemo(() => classifyContent(value), [value]);
  const [raw, setRaw] = useState(false);
  const [copied, setCopied] = useState(false);

  if (classified.kind === "code") {
    return <CodeBlock code={classified.value} language={classified.language} />;
  }
  if (classified.kind === "diff") return <DiffBlock diff={classified.value} />;
  if (classified.kind === "text") {
    return <pre className="plain-output">{classified.value}</pre>;
  }

  const safeValue = toSafeValue(classified.value);
  const serialized = JSON.stringify(safeValue, null, 2);

  async function copy() {
    await navigator.clipboard?.writeText(serialized);
    setCopied(true);
    window.setTimeout(() => setCopied(false), 1400);
  }

  return (
    <section className="structured-value">
      <header className="structured-value-header">
        <span>{label}</span>
        <div>
          <button type="button" aria-pressed={!raw} onClick={() => setRaw(false)}>树</button>
          <button type="button" aria-pressed={raw} onClick={() => setRaw(true)}>原始</button>
          <CardActionButton action="copy" label="复制 JSON" copied={copied} onClick={copy} />
        </div>
      </header>
      {raw ? <CodeBlock code={serialized} language="json" /> : <JsonTree value={safeValue} />}
    </section>
  );
}
