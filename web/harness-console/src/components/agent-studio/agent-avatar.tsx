import type { ReactNode } from "react";
import styles from "./agent-studio.module.css";

const portraits = {
  research: <><path d="M4 5h10v14H4zM7 9h4m-4 4h3" /><circle cx="16" cy="14" r="4" /><path d="m19 17 3 3" /></>,
  meeting: <><path d="M4 4h16v12H9l-5 4zM8 8h8m-8 4h5" /><circle cx="19" cy="19" r="3" /><path d="M19 17v2l1 1" /></>,
  data: <><path d="M4 4v16h17M8 16v-5m5 5V7m5 9v-7m-11-2 5-4 6 2" /></>,
  code: <><rect x="3" y="4" width="18" height="16" rx="3" /><path d="m8 9-3 3 3 3m8-6 3 3-3 3m-3-7-2 9" /></>,
  writing: <><path d="M13 4H5v17h14v-8M8 18h8M10 11l8-8 3 3-8 8-4 1zM16 5l3 3" /></>,
  finance: <><path d="M3 9h18L12 3zM5 12v7m5-7v7m4-7v7m5-7v7M3 21h18" /></>,
  workflow: <><rect x="8" y="2" width="8" height="6" rx="2" /><rect x="2" y="16" width="8" height="6" rx="2" /><rect x="14" y="16" width="8" height="6" rx="2" /><path d="M12 8v4m-6 4v-4h12v4" /></>,
  design: <><path d="M12 3a9 9 0 1 0 0 18h1a2 2 0 0 0 1-4 2 2 0 0 1 1-4h3a3 3 0 0 0 3-3c0-4-4-7-9-7Z" /><circle cx="7" cy="10" r="1" /><circle cx="10" cy="7" r="1" /><circle cx="15" cy="7" r="1" /></>,
} satisfies Record<string, ReactNode>;

type Portrait = keyof typeof portraits;
const purposes: [RegExp, Portrait][] = [
  [/会议|纪要|meeting|minutes/i, "meeting"],
  [/代码|开发|工程|code|developer|engineer/i, "code"],
  [/设计|绘图|design|art|creative/i, "design"],
  [/财务|金融|会计|finance|account|budget/i, "finance"],
  [/数据|报表|统计|data|analytics/i, "data"],
  [/写作|文案|内容|writing|writer|content/i, "writing"],
  [/流程|执行|自动化|workflow|operator|automation/i, "workflow"],
  [/研究|资料|检索|research|search|analyst/i, "research"],
];

export function AgentAvatar({ name, displayName, domain }: { name: string; displayName: string; domain: string }) {
  const identity = `${displayName} ${name} ${domain}`;
  const hash = [...name].reduce((value, char) => (value * 31 + char.codePointAt(0)!) >>> 0, 0);
  const kind = purposes.find(([pattern]) => pattern.test(identity))?.[1]
    ?? (Object.keys(portraits) as Portrait[])[hash % Object.keys(portraits).length];
  return <span className={styles.agentCatalogAvatar} data-purpose={kind} aria-hidden="true">
    <svg viewBox="0 0 24 24" width="26" height="26" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">{portraits[kind]}</svg>
  </span>;
}
