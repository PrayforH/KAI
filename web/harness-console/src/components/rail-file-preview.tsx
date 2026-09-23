"use client";

import { OfficeFilePreview } from "./office-file-preview";
import { officeKindFor, type OfficeKind } from "../lib/office-preview";
import { useEffect, useMemo, useState } from "react";
import { requireAuthenticatedResponse } from "../lib/client-auth";
import { ProjectSourceEditor } from "./agent-studio/project-source-editor";
import { useCodeTheme } from "./agent-studio/project-code-theme";
import { MarkdownText } from "./markdown-text";
import { TextMessagePartProvider } from "@assistant-ui/react";

/** A file is previewed in the rail instead of being downloaded. */
export interface PreviewTarget {
  artifact_id: string;
  name: string;
  media_type: string;
  size_bytes?: number | null;
  thread_id?: string | null;
}

const TEXT_PREVIEW_LIMIT_BYTES = 2 * 1024 * 1024;
const CSV_PREVIEW_ROWS = 200;
/** Kinds the browser renders on its own, so the rail never downloads the bytes. */
const FRAMED_KINDS = new Set<PreviewKind>(["image", "pdf", "html", "none", "xlsx", "docx", "pptx", "legacy-office"]);

export type PreviewKind = OfficeKind | "markdown" | "csv" | "json" | "code" | "html" | "image" | "pdf" | "none";

export function previewKindFor(mediaType: string, name: string): PreviewKind {
  const type = (mediaType || "").toLowerCase();
  const extension = name.toLowerCase().split(".").pop() ?? "";
  const office = officeKindFor(type, name);
  if (office) return office;
  if (type.startsWith("image/")) return "image";
  if (type === "application/pdf" || extension === "pdf") return "pdf";
  if (type === "text/html" || ["html", "htm"].includes(extension)) return "html";
  if (type.includes("markdown") || ["md", "markdown"].includes(extension)) return "markdown";
  if (type === "text/csv" || ["csv", "tsv"].includes(extension)) return "csv";
  if (type === "application/json" || extension === "json") return "json";
  if (type.startsWith("text/") || CODE_EXTENSIONS.has(extension)) return "code";
  return "none";
}

const CODE_EXTENSIONS = new Set([
  "py", "ts", "tsx", "js", "jsx", "sh", "bash", "zsh", "yaml", "yml", "toml", "ini",
  "log", "sql", "css", "xml", "svg", "sed", "awk", "mjs", "cjs", "rs", "go",
]);

/** Minimal RFC4180 reader: enough for a read-only table preview. */
export function parseCsvRows(text: string, limit = CSV_PREVIEW_ROWS): string[][] {
  const rows: string[][] = [];
  let row: string[] = [];
  let field = "";
  let quoted = false;
  for (let index = 0; index < text.length; index += 1) {
    const char = text[index];
    if (quoted) {
      if (char === '"') {
        if (text[index + 1] === '"') {
          field += '"';
          index += 1;
        } else {
          quoted = false;
        }
      } else {
        field += char;
      }
      continue;
    }
    if (char === '"') quoted = true;
    else if (char === ",") { row.push(field); field = ""; }
    else if (char === "\n") {
      row.push(field.replace(/\r$/, ""));
      rows.push(row);
      row = [];
      field = "";
      if (rows.length > limit) return rows;
    } else field += char;
  }
  if (field.length > 0 || row.length > 0) {
    row.push(field.replace(/\r$/, ""));
    rows.push(row);
  }
  return rows.slice(0, limit + 1);
}

function contentUrl(target: PreviewTarget): string {
  const params = new URLSearchParams();
  if (target.thread_id) params.set("thread_id", target.thread_id);
  params.set("preview", "1");
  return `/api/harness/artifacts/${encodeURIComponent(target.artifact_id)}?${params.toString()}`;
}

function downloadUrl(target: PreviewTarget): string {
  const params = new URLSearchParams();
  if (target.thread_id) params.set("thread_id", target.thread_id);
  const query = params.toString();
  return `/api/harness/artifacts/${encodeURIComponent(target.artifact_id)}${query ? `?${query}` : ""}`;
}

function ExternalLinkIcon() {
  return (
    <svg viewBox="0 0 16 16" aria-hidden="true" focusable="false">
      <path d="M6.4 3.6H3.6v8.8h8.8V9.6" />
      <path d="M9.5 3.6h2.9v2.9" />
      <path d="M12.4 3.6 7.3 8.7" />
    </svg>
  );
}

export function RailFilePreview({ target }: { target: PreviewTarget }) {
  const kind = previewKindFor(target.media_type ?? "", target.name ?? "");
  const url = contentUrl(target);
  const theme = useCodeTheme();
  const [text, setText] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(!FRAMED_KINDS.has(kind));

  useEffect(() => {
    if (FRAMED_KINDS.has(kind)) return;
    const controller = new AbortController();
    setText("");
    setError("");
    setLoading(true);
    (async () => {
      try {
        if ((target.size_bytes ?? 0) > TEXT_PREVIEW_LIMIT_BYTES) {
          setError("文件较大，请在系统中打开或下载后查看。");
          return;
        }
        const response = requireAuthenticatedResponse(
          await fetch(url, { cache: "no-store", signal: controller.signal }),
        );
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        const body = await response.text();
        if (!controller.signal.aborted) setText(body);
      } catch (cause) {
        if (!controller.signal.aborted) {
          setError(cause instanceof Error && cause.name !== "AbortError"
            ? "文件内容读取失败，可尝试下载后查看。"
            : "");
        }
      } finally {
        if (!controller.signal.aborted) setLoading(false);
      }
    })();
    return () => controller.abort();
  }, [kind, url, target.size_bytes]);

  const rows = useMemo(
    () => (kind === "csv" && text ? parseCsvRows(text) : []),
    [kind, text],
  );

  return (
    <div className="rail-preview" data-kind={kind}>
      <header className="rail-preview-head">
        <span className="rail-preview-name" title={target.name}>{target.name}</span>
        <div className="rail-preview-actions">
          {/* Every artifact opens in its own tab, previewable or not: the
          authenticated content route answers with inline disposition, so the
          browser renders what it can and downloads the rest. */}
          <a
            className="rail-preview-action"
            href={url}
            target="_blank"
            rel="noreferrer"
            aria-label={`在新标签页打开 ${target.name}`}
            title="在新标签页打开"
          >
            <ExternalLinkIcon />
          </a>
          <a
            className="rail-preview-download"
            href={downloadUrl(target)}
            download={target.name}
            aria-label={`下载 ${target.name}`}
            title={`下载 ${target.name}`}
          >
            ↓
          </a>
        </div>
      </header>
      <div className="rail-preview-body">
        {["xlsx", "docx", "pptx", "legacy-office"].includes(kind) ? (
          <OfficeFilePreview key={url} url={url} kind={kind as OfficeKind} name={target.name} size={target.size_bytes} />
        ) : kind === "image" ? (
          <img className="rail-preview-image" src={url} alt={target.name} />
        ) : kind === "pdf" ? (
          <iframe className="rail-preview-pdf" src={url} title={target.name} />
        ) : kind === "html" ? (
          // Generated pages run without the console origin, so their scripts
          // cannot reach the session even though the artifact loads with it.
          <iframe
            className="rail-preview-html"
            src={url}
            title={target.name}
            sandbox="allow-scripts allow-popups allow-forms allow-modals"
          />
        ) : kind === "none" ? (
          <p className="rail-preview-note">该类型暂不支持在线预览，请下载后查看。</p>
        ) : loading ? (
          <span className="rail-preview-note">正在读取文件…</span>
        ) : error ? (
          <p className="rail-preview-note" role="alert">{error}</p>
        ) : kind === "markdown" ? (
          <div className="rail-preview-markdown">
            <TextMessagePartProvider text={text} isRunning={false}>
              <MarkdownText />
            </TextMessagePartProvider>
          </div>
        ) : kind === "csv" ? (
          <div className="rail-preview-table-wrap">
            <table className="rail-preview-table">
              <tbody>
                {rows.slice(1).map((row, rowIndex) => (
                  <tr key={rowIndex}>
                    {row.map((cell, cellIndex) => (
                      <td key={cellIndex}>{cell}</td>
                    ))}
                  </tr>
                ))}
              </tbody>
              <thead>
                <tr>
                  {(rows[0] ?? []).map((cell, cellIndex) => (
                    <th key={cellIndex}>{cell}</th>
                  ))}
                </tr>
              </thead>
            </table>
            {rows.length > CSV_PREVIEW_ROWS && (
              <p className="rail-preview-note">仅显示前 {CSV_PREVIEW_ROWS} 行</p>
            )}
          </div>
        ) : (
          // Source files read like the code view's editor, so the rail matches it.
          <ProjectSourceEditor
            path={target.name}
            content={kind === "json" ? prettyJson(text) : text}
            theme={theme}
            wrap
          />
        )}
      </div>
    </div>
  );
}

function prettyJson(text: string): string {
  try {
    return JSON.stringify(JSON.parse(text), null, 2);
  } catch {
    return text;
  }
}
