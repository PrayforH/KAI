import type { ReactNode } from "react";
import { ProjectConnectorIcon } from "./project-connector-icon";
import styles from "./project-home.module.css";

const paths = {
  globe: <><circle cx="12" cy="12" r="9" /><ellipse cx="12" cy="12" rx="4" ry="9" /><path d="M3 12h18" /></>,
  branch: <><circle cx="6" cy="5" r="2.5" /><circle cx="18" cy="5" r="2.5" /><circle cx="6" cy="19" r="2.5" /><path d="M6 7.5v9m12-9V10a4 4 0 0 1-4 4H6" /></>,
  book: <><path d="M3 4h5a4 4 0 0 1 4 4v13a4 4 0 0 0-4-3H3Zm18 0h-5a4 4 0 0 0-4 4v13a4 4 0 0 1 4-3h5Z" /></>,
  compass: <><circle cx="12" cy="12" r="9" /><path d="m16 8-3 5-5 3 3-5Z" /></>,
  video: <><rect x="3" y="5" width="13" height="14" rx="2" /><path d="m16 10 5-4v12l-5-4" /></>,
  music: <><path d="M10 18V5l11-2v13M10 9l11-2" /><ellipse cx="6.5" cy="18" rx="3.5" ry="3" /><ellipse cx="17.5" cy="16" rx="3.5" ry="3" /></>,
  code: <><path d="m8 6-6 6 6 6m8-12 6 6-6 6M14 3l-4 18" /></>,
  slides: <><rect x="3" y="3" width="18" height="14" rx="2" /><path d="M12 17v4m-4 0h8M7 12l3-4 3 3 4-5" /></>,
  pen: <><path d="m14 4 6 6M4 20l5-1L21 7a2 2 0 0 0-4-4L5 15Z" /></>,
  skill: <><path d="M9 3H4v6h2a3 3 0 1 1 0 6H4v6h6v-2a3 3 0 1 1 6 0v2h5v-6h-2a3 3 0 1 1 0-6h2V3h-6v2a3 3 0 1 1-6 0Z" /></>,
} satisfies Record<string, ReactNode>;

type Glyph = keyof typeof paths;

function skillGlyph(identity: string): Glyph {
  if (/视频|video/i.test(identity)) return "video";
  if (/音乐|music|audio/i.test(identity)) return "music";
  if (/github|gitlab/i.test(identity)) return "branch";
  if (/ppt|slides|演示/i.test(identity)) return "slides";
  if (/研究|搜索|research|search/i.test(identity)) return "compass";
  if (/设计|design|写作|write/i.test(identity)) return "pen";
  if (/代码|开发|code|coding/i.test(identity)) return "code";
  return "skill";
}

export function ProjectResourceIcon({ kind, name = "" }: {
  kind: "connector" | "skill" | "file" | "link" | "knowledge";
  name?: string;
}) {
  if (kind === "connector") return <ProjectConnectorIcon name={name} />;
  if (kind === "file") {
    const ext = name.split(".").at(-1)?.toLowerCase();
    const format = /^(docx?|odt)$/.test(ext ?? "") ? "word"
      : /^(xlsx?|csv|ods)$/.test(ext ?? "") ? "sheet"
      : /^(pptx?|odp)$/.test(ext ?? "") ? "slides"
      : ext === "pdf" ? "pdf" : "file";
    const label = { word: "W", sheet: "X", slides: "P", pdf: "PDF", file: "" }[format];
    return <span className={styles.fileBadge} data-format={format} aria-hidden="true">
      <svg viewBox="0 0 20 24"><path d="M3 1h9l5 5v17H3Z" /><path d="M12 1v6h5" /></svg>
      {label && <span>{label}</span>}
    </span>;
  }
  const glyph = kind === "skill" ? skillGlyph(name) : kind === "knowledge" ? "book" : "globe";
  return <svg className={styles.resourceGlyph} data-glyph={glyph} viewBox="0 0 24 24" aria-hidden="true">
    {paths[glyph]}
  </svg>;
}
