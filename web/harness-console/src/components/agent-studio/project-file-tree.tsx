"use client";

import { useEffect, useRef, type CSSProperties } from "react";
import { FileTree, useFileTree } from "@pierre/trees/react";
import { themeToTreeStyles, type GitStatusEntry } from "@pierre/trees";
import codexDark from "../../lib/code-themes/codex-dark.json";
import codexLight from "../../lib/code-themes/codex-light.json";

const themes = { dark: themeToTreeStyles({ ...codexDark, type: "dark" }), light: themeToTreeStyles({ ...codexLight, type: "light" }) };
const treeCSS = `
:host {
  --trees-bg-override: var(--source-bg);
  --trees-bg-muted-override: var(--source-hover);
  --trees-border-color-override: var(--source-line);
  --trees-fg-override: var(--source-ink);
  --trees-font-size-override: 13px;
  --trees-font-family-override: var(--source-font-ui);
  --trees-focus-ring-color-override: #0169cc;
  --trees-selected-bg-override: var(--source-hover);
  --trees-selected-fg-override: var(--source-ink);
  --trees-item-padding-x-override: 6px;
  --trees-item-margin-x-override: 0px;
  --trees-level-gap-override: 0px;
  --trees-padding-inline-override: 0px;
  --trees-item-row-gap-override: 10px;
}
[data-item-type='file'] { color: var(--source-muted); }
[data-item-type='file'][data-item-selected] { color: var(--source-ink); }
`;
export function ProjectFileTree({ paths, selected, onSelect, theme, gitStatus }: {
  paths: string[]; selected: string; onSelect: (path: string) => void; theme: "dark" | "light"; gitStatus?: GitStatusEntry[];
}) {
  const selectRef = useRef(onSelect); selectRef.current = onSelect;
  const pathsRef = useRef(paths); pathsRef.current = paths;
  const { model } = useFileTree({ paths, gitStatus, initialExpansion: "open", flattenEmptyDirectories: true,
    icons: { set: "complete", colored: true }, itemHeight: 29, stickyFolders: true, unsafeCSS: treeCSS,
    onSelectionChange: values => { const path = values[values.length - 1]; if (path && pathsRef.current.includes(path)) selectRef.current(path); },
  });
  useEffect(() => { model.setGitStatus(gitStatus); }, [model, gitStatus]);
  useEffect(() => { model.resetPaths(paths); }, [model, paths]);
  useEffect(() => {
    for (const path of model.getSelectedPaths()) if (path !== selected) model.getItem(path)?.deselect();
    const item = model.getItem(selected);
    if (item && !item.isSelected()) item.select();
    if (item) model.scrollToPath(selected, { focus: false });
  }, [model, selected, paths]);
  return <FileTree model={model} style={{ ...themes[theme], height: "100%", colorScheme: theme } as CSSProperties} />;
}
