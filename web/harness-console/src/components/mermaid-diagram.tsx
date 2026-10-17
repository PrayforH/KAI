"use client";
import { CardActionButton } from "./card-action-button";

import type { CodeHeaderProps } from "@assistant-ui/react-markdown";
import { useEffect, useId, useRef, useState } from "react";
import { writeTextToClipboard } from "../lib/clipboard";

type ColorMode = "dark" | "light";

interface RenderedDiagram {
  code: string;
  svg: string;
}

function currentColorMode(): ColorMode {
  return document.documentElement.dataset.colorMode === "light"
    ? "light"
    : "dark";
}

function useColorMode() {
  const [mode, setMode] = useState<ColorMode>("dark");

  useEffect(() => {
    const root = document.documentElement;
    const update = () => setMode(currentColorMode());
    update();
    const observer = new MutationObserver(update);
    observer.observe(root, {
      attributes: true,
      attributeFilter: ["data-color-mode"],
    });
    return () => observer.disconnect();
  }, []);

  return mode;
}

function mermaidTheme(mode: ColorMode) {
  const dark = mode === "dark";
  return {
    background: dark ? "#181818" : "#ffffff",
    primaryColor: dark ? "#242424" : "#f2f2f2",
    primaryTextColor: dark ? "#ffffff" : "#1a1c1f",
    primaryBorderColor: dark ? "#505050" : "#b8b8b8",
    lineColor: dark ? "#929292" : "#686868",
    secondaryColor: dark ? "#202b35" : "#e8f3ff",
    secondaryTextColor: dark ? "#ffffff" : "#1a1c1f",
    secondaryBorderColor: "#339cff",
    tertiaryColor: dark ? "#211d29" : "#f4edff",
    tertiaryTextColor: dark ? "#ffffff" : "#1a1c1f",
    tertiaryBorderColor: dark ? "#ad7bf9" : "#924ff7",
    noteBkgColor: dark ? "#2b261c" : "#fff7df",
    noteTextColor: dark ? "#ffffff" : "#1a1c1f",
    noteBorderColor: dark ? "#8e7446" : "#c89b3c",
    clusterBkg: dark ? "#1d1d1d" : "#fafafa",
    clusterBorder: dark ? "#454545" : "#d0d0d0",
    edgeLabelBackground: dark ? "#181818" : "#ffffff",
    fontFamily:
      '"Avenir Next", "Segoe UI", ui-sans-serif, system-ui, sans-serif',
  };
}

export function MermaidCodeHeader(_props: CodeHeaderProps) {
  return null;
}

/** Renders one Mermaid source. Used both as the markdown fence highlighter and
 * directly by the Intelligent UI diagram block, so it declares only the input it
 * actually reads. */
export function MermaidDiagram({ code }: { code: string }) {
  const colorMode = useColorMode();
  const reactId = useId().replace(/[^a-zA-Z0-9_-]/g, "");
  const renderSequence = useRef(0);
  const diagramRef = useRef<HTMLDivElement>(null);
  const [rendered, setRendered] = useState<RenderedDiagram | null>(null);
  const [pending, setPending] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [showSource, setShowSource] = useState(false);
  const [copied, setCopied] = useState(false);
  const [copyFailed, setCopyFailed] = useState(false);
  const source = code.trim();

  useEffect(() => {
    if (!source) {
      setPending(false);
      setError("图表内容为空");
      return;
    }

    let active = true;
    const sequence = ++renderSequence.current;
    setPending(true);
    setError(null);

    // A Mermaid fence is already mounted while the answer is streaming. Waiting for
    // a short quiet period avoids parsing every token and keeps the last good SVG in
    // place while a diagram is still growing.
    const timer = window.setTimeout(async () => {
      try {
        const { default: mermaid } = await import("mermaid");
        if (!active || sequence !== renderSequence.current) return;

        mermaid.initialize({
          startOnLoad: false,
          securityLevel: "strict",
          suppressErrorRendering: true,
          theme: "base",
          themeVariables: mermaidTheme(colorMode),
          flowchart: {
            htmlLabels: false,
            useMaxWidth: true,
          },
        });

        const result = await mermaid.render(
          `agent-studio-mermaid-${reactId}-${sequence}`,
          source,
        );
        if (!active || sequence !== renderSequence.current) return;

        setRendered({ code: source, svg: result.svg });
        setPending(false);
        setError(null);
        window.requestAnimationFrame(() => {
          if (diagramRef.current) result.bindFunctions?.(diagramRef.current);
        });
      } catch (caught) {
        if (!active || sequence !== renderSequence.current) return;
        setPending(false);
        setError(
          caught instanceof Error ? caught.message : "无法解析 Mermaid 图表",
        );
      }
    }, 420);

    return () => {
      active = false;
      window.clearTimeout(timer);
    };
  }, [colorMode, reactId, source]);

  async function copySource() {
    const success = await writeTextToClipboard(source);
    setCopied(success);
    setCopyFailed(!success);
    if (success) window.setTimeout(() => setCopied(false), 1400);
  }

  function downloadSvg() {
    if (!rendered?.svg) return;
    const blob = new Blob([rendered.svg], {
      type: "image/svg+xml;charset=utf-8",
    });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = "agent-studio-diagram.svg";
    anchor.click();
    window.setTimeout(() => URL.revokeObjectURL(url), 0);
  }

  const hasCurrentDiagram = rendered?.code === source;
  const accessibleStatus = error
    ? "Mermaid 图表渲染失败，已显示源码"
    : pending && !hasCurrentDiagram
      ? "正在生成 Mermaid 图表"
      : "Mermaid 图表";

  return (
    <section className="mermaid-card" aria-label={accessibleStatus}>
      <header className="mermaid-card-header">
        <span className="mermaid-card-title">
          <i aria-hidden="true" />
          Mermaid
          {pending && rendered ? <small>更新中</small> : null}
        </span>
        <span className="mermaid-card-actions">
          <button
            type="button"
            onClick={() => setShowSource((value) => !value)}
            aria-pressed={showSource}
          >
            {showSource ? "图表" : "源码"}
          </button>
          <CardActionButton action="copy" label="复制图表源码" copied={copied} onClick={copySource} />
          <CardActionButton action="download" label="下载 SVG" onClick={downloadSvg} disabled={!rendered} />
        </span>
      </header>

      {copyFailed ? (
        <p className="mermaid-error" role="alert">
          复制失败，请查看源码后手动复制。
        </p>
      ) : null}

      {showSource || (error && !rendered) ? (
        <pre className="mermaid-source">
          <code>{source}</code>
        </pre>
      ) : rendered ? (
        <div
          ref={diagramRef}
          className="mermaid-canvas"
          role="img"
          aria-label="任务回答生成的 Mermaid 可视化"
          dangerouslySetInnerHTML={{ __html: rendered.svg }}
        />
      ) : (
        <div className="mermaid-loading" aria-live="polite">
          <span />
          <span />
          <span />
        </div>
      )}

      {error ? (
        <p className="mermaid-error" role="status">
          图表语法暂时无法渲染，可查看并复制源码。
        </p>
      ) : null}
    </section>
  );
}
