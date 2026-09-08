"use client";
import { useInternalAgentsPreference } from "../../lib/interface-preferences";

import { usePathname, useRouter } from "next/navigation";
import { useEffect, useState, type ReactNode } from "react";
import { useAuth } from "../auth-provider";
import { TaskSidebar } from "../task-sidebar";
import { ProductivityCommandCenter } from "../productivity-command-center";
import { loadTaskAgentCatalog, type TaskAgent } from "../../lib/task-agent-catalog";
import type { TaskSummary } from "../../lib/task-history";
import type { WorkspaceId } from "../workspace-navigation";

/**
 * The one shared shell for every Studio route. It renders the conversation
 * window's left sidebar (brand / new-task / search / 智能体 / 技能·MCP / 项目)
 * exactly as on the task page, and keeps it fixed while `children` (the center
 * management page) changes. Only the highlighted nav item varies by route.
 */
export function StudioUnifiedShell({
  activeNav,
  children,
}: {
  activeNav?: WorkspaceId;
  children: ReactNode;
}) {
  const router = useRouter();
  const pathname = usePathname();
  const { user } = useAuth();
  const [showInternalAgents] = useInternalAgentsPreference();
  const [collapsed, setCollapsed] = useState(false);
  const [agents, setAgents] = useState<TaskAgent[]>([]);

  useEffect(() => {
    let active = true;
    loadTaskAgentCatalog(user.user_id, showInternalAgents)
      .then((catalog) => {
        if (active) setAgents(catalog.agents);
      })
      .catch(() => {
        if (active) setAgents([]);
      });
    return () => {
      active = false;
    };
  }, [user.user_id, showInternalAgents]);

  const goHome = () => router.push("/");
  const routeWorkspace: WorkspaceId = pathname.startsWith("/studio/files")
    ? "files"
    : pathname.startsWith("/studio/skills")
      ? "skills"
      : pathname.startsWith("/studio/capabilities")
        ? "capabilities"
        : pathname.startsWith("/studio/knowledge")
          ? "knowledge"
          : pathname.startsWith("/studio/usage")
            ? "usage"
            : pathname.startsWith("/studio/data")
              ? "data"
              : "agents";

  return (
    <main className="console-shell studio-unified" id="main-content">
      <TaskSidebar
        currentThreadId=""
        collapsed={collapsed}
        onToggle={() => setCollapsed((current) => !current)}
        onSelect={(task: TaskSummary) =>
          router.push(`/?thread=${encodeURIComponent(task.thread_id)}`)
        }
        onNewTask={goHome}
        onNewTaskWithProject={(task: TaskSummary) =>
          router.push(`/?thread=${encodeURIComponent(task.thread_id)}`)
        }
        searchControl={
          <ProductivityCommandCenter
            agents={agents}
            onNewTask={goHome}
            onSelectTask={(task: TaskSummary) =>
              router.push(`/?thread=${encodeURIComponent(task.thread_id)}`)
            }
            onStartWithAgent={() => goHome()}
          />
        }
        activeNav={activeNav ?? routeWorkspace}
      />
      <div
        className="studio-unified-content"
        data-sidebar-collapsed={collapsed ? "true" : "false"}
      >
        {collapsed && (
          <button
            type="button"
            className="header-icon-button header-sidebar-toggle studio-sidebar-expand"
            aria-label="展开任务列表"
            aria-expanded="false"
            title="展开任务列表"
            onClick={() => setCollapsed(false)}
          >
            <svg viewBox="0 0 20 20" aria-hidden="true">
              <rect x="2.75" y="4.25" width="14.5" height="11.5" rx="2.5" />
              <path d="M6.75 4.25v11.5" />
            </svg>
          </button>
        )}
        {children}
      </div>
    </main>
  );
}
