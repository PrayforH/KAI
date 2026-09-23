"use client";

import { useEffect, useRef, useState } from "react";
import { requireAuthenticatedResponse } from "../lib/client-auth";
import { OFFICE_PREVIEW_LIMIT, readSpreadsheet, SHEET_COLUMN_LIMIT, SHEET_ROW_LIMIT, spreadsheetColumn, validateOfficeArchive, type OfficeKind, type SheetPreview } from "../lib/office-preview";

// Rendered Office content cannot execute scripts, load remote assets, or affect
// the console's CSS. Only our parent-side renderer can write into this frame.
const DOCUMENT_FRAME = `<!doctype html><html><head><meta charset="utf-8"><meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; img-src data: blob:; font-src data: blob:; base-uri 'none'; form-action 'none'"><style>html,body{margin:0;background:#e5e7eb;color:#171717;font-family:Arial,sans-serif}#document{width:fit-content;min-width:100%;transform-origin:top left}#document .docx-wrapper{padding:12px;background:transparent}#document .docx-wrapper>section.docx{box-shadow:0 1px 6px #0002;margin-bottom:12px}#document .pptx-preview-wrapper{height:auto!important;overflow:visible!important;background:transparent!important}*{box-sizing:border-box}</style></head><body><div id="styles"></div><div id="document"></div></body></html>`;

function DocumentPreview({ buffer, kind, name }: { buffer: ArrayBuffer; kind: "docx" | "pptx"; name: string }) {
  const frame = useRef<HTMLIFrameElement>(null);
  const [ready, setReady] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  useEffect(() => {
    const iframe = frame.current;
    const document = iframe?.contentDocument;
    const stage = document?.getElementById("document");
    if (!iframe || !document || !stage || !ready) return;
    let cancelled = false;
    let destroy: (() => void) | undefined;
    let resize: ResizeObserver | undefined;
    setLoading(true); setError("");
    const blockLink = (event: MouseEvent) => {
      if ((event.target as Element | null)?.closest?.("a")) event.preventDefault();
    };
    document.addEventListener("click", blockLink);
    void (async () => {
      try {
        if (kind === "docx") {
          const { renderAsync } = await import("docx-preview");
          if (cancelled) return;
          await renderAsync(buffer, stage, document.getElementById("styles")!, { useBase64URL: true, renderAltChunks: false });
        } else {
          const { init } = await import("pptx-preview");
          if (cancelled) return;
          const preview = init(stage, { width: 960, height: 540, mode: "list" });
          destroy = () => preview.destroy();
          await preview.preview(buffer);
        }
        if (cancelled) return;
        const fit = () => {
          stage.style.zoom = "1";
          stage.style.zoom = String(Math.min(1, Math.max(1, iframe.clientWidth - 2) / Math.max(1, stage.scrollWidth)));
        };
        fit(); resize = new ResizeObserver(fit); resize.observe(iframe);
      } catch {
        if (!cancelled) setError("文档预览失败，文件可能损坏或加密。可下载后查看。");
      } finally { if (!cancelled) setLoading(false); }
    })();
    return () => { cancelled = true; resize?.disconnect(); destroy?.(); document.removeEventListener("click", blockLink); };
  }, [buffer, kind, ready]);
  return <div className="rail-office-document">
    {loading && <p className="rail-preview-note" role="status">正在排版文档…</p>}
    {error && <p className="rail-preview-note" role="alert">{error}</p>}
    <iframe ref={frame} title={name} className="rail-office-frame" sandbox="allow-same-origin" srcDoc={DOCUMENT_FRAME} onLoad={() => setReady(value => value + 1)} hidden={Boolean(error)} />
  </div>;
}

function SpreadsheetPreview({ sheets }: { sheets: SheetPreview[] }) {
  const [selected, setSelected] = useState(0);
  const sheet = sheets[selected];
  if (!sheet) return <p className="rail-preview-note">没有可显示的工作表。</p>;
  return <div className="rail-office-sheet">
    <div className="rail-office-tabs" role="tablist" aria-label="工作表">
      {sheets.map((item, index) => <button key={item.name} type="button" role="tab" aria-selected={selected === index} onClick={() => setSelected(index)}>{item.name}</button>)}
    </div>
    <div className="rail-preview-table-wrap" role="tabpanel" aria-label={sheet.name}>
      <table className="rail-preview-table"><thead><tr><th aria-label="行号" />{(sheet.rows[0] ?? []).map((_, index) => <th key={index}>{spreadsheetColumn(index)}</th>)}</tr></thead>
        <tbody>{sheet.rows.map((row, index) => <tr key={index}><th scope="row">{index + 1}</th>{row.map((cell, column) => <td key={column}>{cell}</td>)}</tr>)}</tbody>
      </table>
      {!sheet.totalRows && <p className="rail-preview-note">空白工作表</p>}
    </div>
    <p className="rail-office-sheet-count">{sheet.totalRows} 行 · {sheet.totalColumns} 列{sheet.totalRows > SHEET_ROW_LIMIT || sheet.totalColumns > SHEET_COLUMN_LIMIT ? ` · 预览前 ${SHEET_ROW_LIMIT} 行、${SHEET_COLUMN_LIMIT} 列` : ""}</p>
  </div>;
}

export function OfficeFilePreview({ url, kind, name, size }: { url: string; kind: OfficeKind; name: string; size?: number | null }) {
  const [buffer, setBuffer] = useState<ArrayBuffer | null>(null);
  const [sheets, setSheets] = useState<SheetPreview[] | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    if (kind === "legacy-office") return;
    const controller = new AbortController();
    setBuffer(null); setSheets(null); setError("");
    void (async () => {
      try {
        if ((size ?? 0) > OFFICE_PREVIEW_LIMIT) throw new Error("文件超过 50 MB，请下载后查看。");
        const response = requireAuthenticatedResponse(await fetch(url, { signal: controller.signal, cache: "no-store" }));
        if (!response.ok) throw new Error("文件读取失败，请重试或下载后查看。");
        const bytes = await response.arrayBuffer();
        if (controller.signal.aborted) return;
        validateOfficeArchive(bytes);
        if (kind === "xlsx") {
          const result = await readSpreadsheet(bytes);
          if (!controller.signal.aborted) setSheets(result);
        } else setBuffer(bytes);
      } catch (cause) {
        if (!controller.signal.aborted) setError(cause instanceof Error ? cause.message : "文件预览失败，请下载后查看。");
      }
    })();
    return () => controller.abort();
  }, [url, kind, size]);
  if (kind === "legacy-office") return <p className="rail-preview-note">这是旧版 Office 格式，请另存为 .xlsx、.docx 或 .pptx 后预览，也可直接下载。</p>;
  if (error) return <p className="rail-preview-note" role="alert">{error}</p>;
  if (kind === "xlsx" && sheets) return <SpreadsheetPreview sheets={sheets} />;
  if (buffer && kind !== "xlsx") return <DocumentPreview buffer={buffer} kind={kind} name={name} />;
  return <p className="rail-preview-note" role="status">正在读取文件…</p>;
}
