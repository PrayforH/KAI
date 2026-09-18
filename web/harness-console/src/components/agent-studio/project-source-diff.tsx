"use client";
import { useMemo } from "react";
import { FileDiff, Virtualizer } from "@pierre/diffs/react";
import { parseDiffFromFile } from "@pierre/diffs";
import type { ProjectSourceChange } from "../../lib/project-source-changes";
import { codeMetrics, codeThemes, useCodeHighlight } from "./project-code-theme";
import { ProjectSourceEditor } from "./project-source-editor";
import styles from "./agent-project-code.module.css";
export function ProjectSourceDiff({ change, theme, wrap }: { change: ProjectSourceChange; theme: "dark" | "light"; wrap: boolean }) {
  const { ready, failed } = useCodeHighlight(change.path);
  const diff = useMemo(() => {
    if ((change.before && change.before.content == null) || (change.after && change.after.content == null)) return null;
    try {
      const patchOptions = { context: 3, maxEditLength: 10_000, timeout: 250 };
      return parseDiffFromFile(change.before ? { name: change.path, contents: change.before.content! } : null,
        change.after ? { name: change.path, contents: change.after.content! } : null, patchOptions);
    } catch { return null; }
  }, [change]);
  const fallback = change.after ?? change.before;
  if (!diff || failed) return <><p className={styles.diffSummary}>该文件已{change.status === "added" ? "新增" : change.status === "deleted" ? "删除" : "修改"}，暂无法计算行级差异。{fallback?.content != null ? "下方展示完整内容。" : fallback?.unavailable}</p>{fallback?.content != null && <ProjectSourceEditor path={change.path} content={fallback.content} theme={theme} wrap={wrap}/>}</>;
  const added = diff.hunks.reduce((sum, hunk) => sum + hunk.additionLines, 0);
  const deleted = diff.hunks.reduce((sum, hunk) => sum + hunk.deletionLines, 0);
  return <><div className={styles.diffSummary}><span className={styles.added}>+{added}</span><span className={styles.deleted}>−{deleted}</span><span>未修改行已折叠，可展开查看</span></div>
    {!ready ? <div className={styles.loading} role="status">正在加载差异…</div> : <Virtualizer key={change.path} className={styles.editor}><FileDiff fileDiff={diff} metrics={codeMetrics} options={{
      theme: codeThemes, themeType: theme, diffStyle: "unified", disableFileHeader: true,
      overflow: wrap ? "wrap" : "scroll", hunkSeparators: "line-info",
    }} /></Virtualizer>}
  </>;
}
