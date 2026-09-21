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
          ["overview", "概览", "▦"],
          ["playground", "Playground", "◈"],
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
      {draftId && (
        <details
          className={styles.qualityNav}
          open={!["overview", "playground", "sessions"].includes(active)}
        >
          <summary>质量与运营</summary>
          {[
            ["diagnostics", "运行诊断"],
            ["experiments", "改进实验"],
            ["evaluation", "评测验收"],
            ["release", "版本与部署"],
            ["datasets", "评测集"],
            ["experience", "经验库"],
            ["automation", "自动运行"],
            ["integrations", "集成目录"],
          ].map(([id, title]) => (
            <Link
              key={id}
              href={href(id)}
              aria-current={active === id ? "page" : undefined}
            >
              {title}
            </Link>
          ))}
        </details>
      )}
      <div className={styles.navFoot}>
        <Link href="/studio/skills">Skills 与 skill-creator ↗</Link>
        <Link href="/studio/capabilities">模型与集成设置 ↗</Link>
      </div>
    </aside>
  );
}
