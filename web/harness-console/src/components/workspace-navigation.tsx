import { SidebarIcon } from "./sidebar-icon";
import Link from "next/link";
import styles from "./workspace-navigation.module.css";

export type WorkspaceId =
  | "tasks"
  | "agents"
  | "files"
  | "automation"
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
  { id: "automation", href: "/studio/automation", label: "自动化任务" },
  { id: "knowledge", href: "/studio/knowledge", label: "知识库" },
  { id: "skills", href: "/studio/skills", label: "技能" },
];

export function WorkspaceIcon({ workspace }: { workspace: WorkspaceId }) {
  return <SidebarIcon name={workspace} />;
}

export function WorkspaceCollapseIcon(_props: { collapsed: boolean }) {
  return <SidebarIcon name="panel" className={styles.collapseIcon} />;
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
  // `visible` is an ordered allowlist: callers that reorder their sidebar (for
  // example putting the knowledge base directly under 新建任务) get that order.
  const items = visible
    ? visible.flatMap((id) => workspaceItems.filter((workspace) => workspace.id === id))
    : workspaceItems;

  function renderWorkspaceLink(workspace: (typeof workspaceItems)[number]) {
    const current = workspace.id === active;
    const label = labelOverrides?.[workspace.id] ?? workspace.label;
    return (
      <Link
        className={current ? styles.navigationActive : styles.navigationLink}
        href={workspace.href}
        prefetch={false}
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
