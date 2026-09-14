"use client";

import { useEffect, useMemo, useState } from "react";
import { File } from "@pierre/diffs/react";
import { registerCustomTheme, preloadHighlighter, getFiletypeFromFileName, type ThemeRegistration } from "@pierre/diffs";
import codexDark from "../../lib/code-themes/codex-dark.json";
import codexLight from "../../lib/code-themes/codex-light.json";
import styles from "./agent-project-code.module.css";

// The actual Codex TextMate themes, shared by source rendering and the file tree.
const registration = globalThis as typeof globalThis & { __kaiCodexThemesRegistered?: boolean };
if (!registration.__kaiCodexThemesRegistered) {
registerCustomTheme("kai-codex-dark", async () => ({ ...codexDark, name: "kai-codex-dark" }) as ThemeRegistration);
registerCustomTheme("kai-codex-light", async () => ({ ...codexLight, name: "kai-codex-light" }) as ThemeRegistration);
registration.__kaiCodexThemesRegistered = true;
}

export function ProjectSourceEditor({ path, content, theme, wrap }: {
  path: string; content: string; theme: "dark" | "light"; wrap: boolean;
}) {
  const [readyPath, setReadyPath] = useState<string | null>(null);
  const [highlightFailed, setHighlightFailed] = useState(false);
  useEffect(() => {
    let cancelled = false;
    setHighlightFailed(false);
    void preloadHighlighter({ themes: ["kai-codex-dark", "kai-codex-light"], langs: [getFiletypeFromFileName(path)] })
      .then(() => { if (!cancelled) setReadyPath(path); })
      .catch(() => { if (!cancelled) setHighlightFailed(true); });
    return () => { cancelled = true; };
  }, [path]);
  const file = useMemo(() => ({ name: path, contents: content }), [path, content]);
  return <div className={styles.editor} tabIndex={0} aria-label={`${path} 源代码`}>
    {highlightFailed ? <pre className={styles.plainSource}>{content}</pre> : readyPath !== path ? <div className={styles.loading} role="status">正在加载语法高亮…</div> : <File key={path} file={file} options={{
      theme: { dark: "kai-codex-dark", light: "kai-codex-light" },
      themeType: theme, disableFileHeader: true, overflow: wrap ? "wrap" : "scroll",
    }} />}
  </div>;
}
