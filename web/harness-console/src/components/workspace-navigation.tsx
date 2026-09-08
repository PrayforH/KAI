import Link from "next/link";
import styles from "./workspace-navigation.module.css";

export type WorkspaceId =
  | "tasks"
  | "agents"
  | "files"
  | "capabilities"
  | "knowledge"
  | "skills"
  | "spaces"
  | "usage"
  | "data";

export const workspaceItems: ReadonlyArray<{
  id: WorkspaceId;
  href: string;
  label: string;
}> = [
  { id: "tasks", href: "/", label: "任务" },
  { id: "agents", href: "/studio/agents", label: "智能体" },
  { id: "files", href: "/studio/files", label: "我的文件" },
  { id: "capabilities", href: "/studio/capabilities", label: "MCP 能力" },
  { id: "knowledge", href: "/studio/knowledge", label: "知识库" },
  { id: "skills", href: "/studio/skills", label: "技能" },
];

export function WorkspaceIcon({ workspace }: { workspace: WorkspaceId }) {
  if (workspace === "tasks") {
    return (
      <svg viewBox="0 0 20 20" aria-hidden="true">
        <path d="M4.5 5.5h11M4.5 10h11M4.5 14.5h7" />
      </svg>
    );
  }
  if (workspace === "agents") {
    return (
      <svg viewBox="0 0 20 20" aria-hidden="true">
        <circle cx="6" cy="6" r="2" />
        <circle cx="14" cy="6" r="2" />
        <circle cx="10" cy="14" r="2" />
        <path d="m7.7 7.1 1.4 4.8m3.2-4.8-1.4 4.8M8 6h4" />
      </svg>
    );
  }
  if (workspace === "files") {
    return (
      <svg viewBox="0 0 20 20" aria-hidden="true">
        <path d="M4.5 3.5h7l4 4v9h-11z" />
        <path d="M11.5 3.5v4h4M7.5 11h5m-5 2.8h5" />
      </svg>
    );
  }
  if (workspace === "usage") {
    return (
      <svg viewBox="0 0 20 20" aria-hidden="true">
        <path d="M4.5 15.5V11h3v4.5m2-8v8h3v-8m2-3v11h3v-11" />
      </svg>
    );
  }
  if (workspace === "capabilities") {
    return (
      <svg viewBox="0 0 20 20" aria-hidden="true">
        <rect x="7" y="7" width="6" height="6" rx="1.5" />
        <path d="M9 3v4m2-4v4M9 13v4m2-4v4M3 9h4m-4 2h4m6-2h4m-4 2h4" />
      </svg>
    );
  }
  if (workspace === "knowledge") {
    return (
      <svg viewBox="0 0 20 20" aria-hidden="true">
        <path d="M4.5 4.5h7a3 3 0 0 1 3 3v8h-7a3 3 0 0 1-3-3z" />
        <path d="M7.5 7.5h4m-4 3h4" />
      </svg>
    );
  }
  if (workspace === "skills") {
    return (
      <svg viewBox="0 0 20 20" aria-hidden="true">
        <path d="M4.5 4h7.5a3.5 3.5 0 0 1 3.5 3.5v8.5H6.5A2 2 0 0 1 4.5 14z" />
        <path d="M8 8.5h4M8 11h4" />
      </svg>
    );
  }
  if (workspace === "spaces") {
    return (
      <svg viewBox="0 0 20 20" aria-hidden="true">
        <circle cx="7" cy="7" r="2.5" />
        <circle cx="14" cy="8" r="2" />
        <path d="M2.8 16c.5-3 2-4.5 4.3-4.5s3.8 1.5 4.3 4.5m.1-3.5c.7-.8 1.5-1.2 2.5-1.2 1.8 0 3 1.2 3.4 3.5" />
      </svg>
    );
  }
  return (
    <svg viewBox="0 0 20 20" aria-hidden="true">
      <ellipse cx="10" cy="5.5" rx="5.5" ry="2.5" />
      <path d="M4.5 5.5v4c0 1.4 2.5 2.5 5.5 2.5s5.5-1.1 5.5-2.5v-4m-11 4v4c0 1.4 2.5 2.5 5.5 2.5s5.5-1.1 5.5-2.5v-4" />
    </svg>
  );
}

export function WorkspaceCollapseIcon({
  collapsed,
}: {
  collapsed: boolean;
}) {
  return (
    <svg className={styles.collapseIcon} viewBox="0 0 20 20" aria-hidden="true">
      <path
        d={
          collapsed
            ? "m8.5 4 6 6-6 6m-5-12 6 6-6 6"
            : "m11.5 4-6 6 6 6m5-12-6 6 6 6"
        }
      />
    </svg>
  );
}

export function WorkspaceNavigation({
  active,
  collapsed = false,
  visible,
  labelOverrides,
}: {
  active: WorkspaceId;
  collapsed?: boolean;
  visible?: readonly WorkspaceId[];
  labelOverrides?: Partial<Record<WorkspaceId, string>>;
}) {
  const items = visible
    ? workspaceItems.filter((workspace) => visible.includes(workspace.id))
    : workspaceItems;

  function renderWorkspaceLink(workspace: (typeof workspaceItems)[number]) {
    const current = workspace.id === active;
    const label = labelOverrides?.[workspace.id] ?? workspace.label;
    return (
      <Link
        className={current ? styles.navigationActive : styles.navigationLink}
        href={workspace.href}
        aria-current={current ? "page" : undefined}
        title={collapsed ? label : undefined}
        key={workspace.id}
      >
        <WorkspaceIcon workspace={workspace.id} />
        {!collapsed && <span>{label}</span>}
      </Link>
    );
  }

  return (
    <nav
      className={styles.navigation}
      data-workspace-navigation={collapsed ? "collapsed" : "expanded"}
      aria-label="工作区"
    >
      {items.map(renderWorkspaceLink)}
    </nav>
  );
}
