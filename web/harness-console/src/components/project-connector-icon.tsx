import type { ReactNode } from "react";
import styles from "./project-home.module.css";

// Lucide 0.475.0 glyphs (ISC / Feather MIT); notices in public/licenses/lucide.txt.
// Keep this small subset local so connectors require no external icon requests.
const glyphs = {
  database: <> <ellipse cx="12" cy="5" rx="9" ry="3" /><path d="M3 5V19A9 3 0 0 0 21 19V5" /><path d="M3 12A9 3 0 0 0 21 12" /> </>,
  map: <> <path d="M20 10c0 4.993-5.539 10.193-7.399 11.799a1 1 0 0 1-1.202 0C9.539 20.193 4 14.993 4 10a8 8 0 0 1 16 0" /><circle cx="12" cy="10" r="3" /> </>,
  globe: <> <circle cx="12" cy="12" r="10" /><path d="M12 2a14.5 14.5 0 0 0 0 20 14.5 14.5 0 0 0 0-20" /><path d="M2 12h20" /> </>,
  plug: <> <path d="M12 22v-5" /><path d="M9 8V2" /><path d="M15 8V2" /><path d="M18 8v5a4 4 0 0 1-4 4h-4a4 4 0 0 1-4-4V8Z" /> </>,
  mail: <> <rect width="20" height="16" x="2" y="4" rx="2" /><path d="m22 7-8.97 5.7a1.94 1.94 0 0 1-2.06 0L2 7" /> </>,
  book: <> <path d="M12 7v14" /><path d="M3 18a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1h5a4 4 0 0 1 4 4 4 4 0 0 1 4-4h5a1 1 0 0 1 1 1v13a1 1 0 0 1-1 1h-6a3 3 0 0 0-3 3 3 3 0 0 0-3-3z" /> </>,
  github: <> <path d="M15 22v-4a4.8 4.8 0 0 0-1-3.5c3 0 6-2 6-5.5.08-1.25-.27-2.48-1-3.5.28-1.15.28-2.35 0-3.5 0 0-1 0-3 1.5-2.64-.5-5.36-.5-8 0C6 2 5 2 5 2c-.3 1.15-.3 2.35 0 3.5A5.403 5.403 0 0 0 4 9c0 3.5 3 5.5 6 5.5-.39.49-.68 1.05-.85 1.65-.17.6-.22 1.23-.15 1.85v4" /><path d="M9 18c-4.51 2-5-2-7-2" /> </>,
  branch: <> <circle cx="12" cy="18" r="3" /><circle cx="6" cy="6" r="3" /><circle cx="18" cy="6" r="3" /><path d="M18 9v2c0 .6-.4 1-1 1H7c-.6 0-1-.4-1-1V9" /><path d="M12 12v3" /> </>,
} satisfies Record<string, ReactNode>;

function connectorGlyph(identity: string): keyof typeof glyphs {
  if (/database|mysql|postgres|sqlite|数据库|达梦/i.test(identity)) return "database";
  if (/amap|地图|高德|baidu.*map/i.test(identity)) return "map";
  if (/github/i.test(identity)) return "github";
  if (/gitlab|gitee/i.test(identity)) return "branch";
  if (/mail|outlook|邮件|邮箱/i.test(identity)) return "mail";
  if (/knowledge|weknora|知识/i.test(identity)) return "book";
  if (/search|fetch|crawl|web|搜索|网页/i.test(identity)) return "globe";
  return "plug";
}

export function ProjectConnectorIcon({ name }: { name: string }) {
  const glyph = connectorGlyph(name);
  return <svg className={styles.resourceGlyph} data-connector-icon={glyph} viewBox="0 0 24 24" aria-hidden="true">
    {glyphs[glyph]}
  </svg>;
}
