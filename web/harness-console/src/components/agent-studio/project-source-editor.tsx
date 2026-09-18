"use client";
import { useMemo } from "react";
import { File, Virtualizer } from "@pierre/diffs/react";
import { codeMetrics, codeThemes, useCodeHighlight } from "./project-code-theme";
import styles from "./agent-project-code.module.css";
export function ProjectSourceEditor({ path, content, theme, wrap }: {
  path: string; content: string; theme: "dark" | "light"; wrap: boolean;
}) {
  const { ready, failed } = useCodeHighlight(path);
  const file = useMemo(() => ({ name: path, contents: content }), [path, content]);
  return <div className={styles.sourceFrame} tabIndex={0} aria-label={`${path} 源代码`}>
    {failed ? <pre className={styles.plainSource}>{content}</pre> : !ready ? <div className={styles.loading} role="status">正在加载语法高亮…</div> :
      <Virtualizer key={path} className={styles.editor}><File file={file} metrics={codeMetrics} options={{
        theme: codeThemes, themeType: theme, disableFileHeader: true, overflow: wrap ? "wrap" : "scroll",
      }} /></Virtualizer>}
  </div>;
}
