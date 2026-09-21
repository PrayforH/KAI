"use client";
import Link from "next/link";
import styles from "./agenta-workspace.module.css";
export function AgentNavigation({
  name,
  label,
  draftId,
  active,
  onBack,
}: {
  name: string;
  label: string;
  draftId?: string;
  active: string;
  onBack?: () => void;
}) {
  const href = (section: string) =>
    `/studio/agents/${encodeURIComponent(name)}?${new URLSearchParams({ section, ...(draftId ? { draft: draftId } : {}) })}`;
  return (
    <aside className={styles.navigation} aria-label="智能体导航">
      {onBack ? (
        <button className={styles.back} onClick={onBack}>
          ← 返回智能体
        </button>
      ) : (
        <Link className={styles.back} href="/studio/agents">
          ← 返回智能体
        </Link>
      )}
      <div className={styles.agentIdentity}>
        <span className={styles.agentIcon}>◇</span>
        <div>
          <strong>{label || "新建智能体"}</strong>
          <small>Agent</small>
        </div>
      </div>
      <nav>
        {[
          ["playground", "工作台", "◈"],
          ["sessions", "会话", "☷"],
        ].map(([id, title, icon]) =>
          draftId ? (
            <Link
              key={id}
              href={href(id)}
              aria-current={active === id ? "page" : undefined}
            >
              <span aria-hidden="true">{icon}</span>
              {title}
            </Link>
          ) : (
            <button
              key={id}
              disabled
              aria-current={active === id ? "page" : undefined}
            >
              <span aria-hidden="true">{icon}</span>
              {title}
            </button>
          ),
        )}
      </nav>
      <div className={styles.navFoot}>
        <Link href="/studio/skills">Skills 与 skill-creator ↗</Link>
        <Link href="/studio/capabilities">模型与集成设置 ↗</Link>
      </div>
    </aside>
  );
}
