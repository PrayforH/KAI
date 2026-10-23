"use client";
import { SidebarIcon } from "../sidebar-icon";
import { useInternalAgentsPreference } from "../../lib/interface-preferences";

import { usePathname, useRouter } from "next/navigation";
import { useEffect, useRef, useState, type ReactNode } from "react";
import { useAuth } from "../auth-provider";
import { TaskSidebar } from "../task-sidebar";
import { ProductivityCommandCenter } from "../productivity-command-center";
import { loadTaskAgentCatalog, type TaskAgent } from "../../lib/task-agent-catalog";
import { createProjectTask } from "../../lib/project-task";
import { useSidebarProjects } from "../../lib/sidebar-state";
import { createNewThread, createUserScopedStorage } from "../../lib/thread-store";
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
  const navigationRevision = useRef(0);
  const pathname = usePathname();
  const projectPage = pathname.startsWith("/studio/projects/");
  const { user } = useAuth();
  const [showInternalAgents] = useInternalAgentsPreference();
  const [collapsed, setCollapsed] = useState(false);
  const [compactProject, setCompactProject] = useState(false);
  const [agents, setAgents] = useState<TaskAgent[]>([]);
  const [defaultAgent, setDefaultAgent] = useState<TaskAgent | null>(null);
  const [projects, refreshProjects] = useSidebarProjects(user.user_id);

  useEffect(() => {
    if (!projectPage) { setCompactProject(false); return; }
    const media = window.matchMedia("(max-width: 1099px)");
    const sync = () => { setCompactProject(media.matches); setCollapsed(media.matches); };
    sync();
    media.addEventListener("change", sync);
    return () => media.removeEventListener("change", sync);
  }, [projectPage]);

  useEffect(() => {
    let active = true;
    loadTaskAgentCatalog(user.user_id, showInternalAgents)
      .then((catalog) => {
        if (active) { setAgents(catalog.agents); setDefaultAgent(catalog.defaultAgent); }
      })
      .catch(() => {
        if (active) setAgents([]);
      });
    return () => {
      active = false;
    };
  }, [user.user_id, showInternalAgents]);

  const selectTask = (task: TaskSummary) => {
    navigationRevision.current += 1;
    router.push(`/?thread=${encodeURIComponent(task.thread_id)}`);
  };
  const goHome = () => {
    navigationRevision.current += 1;
    const id = createNewThread(createUserScopedStorage(window.localStorage, user.user_id));
    router.push(`/?thread=${encodeURIComponent(id)}`);
  };
  const routeWorkspace: WorkspaceId = pathname.startsWith("/studio/projects")
    ? "tasks"
    : pathname.startsWith("/studio/files")
    ? "files"
    : pathname.startsWith("/studio/automation")
      ? "automation"
      : pathname.startsWith("/studio/skills")
      ? "skills"
        : pathname.startsWith("/studio/knowledge")
          ? "knowledge"
          : pathname.startsWith("/studio/usage")
            ? "usage"
            : pathname.startsWith("/studio/data")
              ? "data"
              : "agents";

  return (
    <main className="console-shell studio-unified" id="main-content" data-project-home={projectPage || undefined}
      onClick={(event) => {
        if (compactProject && event.target instanceof Element && event.target.closest("a[href]")) setCollapsed(true);
      }}>
      {compactProject && !collapsed && <button type="button" className="task-sidebar-scrim" aria-label="关闭任务列表" onClick={() => setCollapsed(true)} />}
      <TaskSidebar
        currentThreadId=""
        collapsed={collapsed}
        overlayOpen={compactProject && !collapsed}
        onToggle={() => setCollapsed((current) => !current)}
        onSelect={selectTask}
        onNewTask={goHome}
        projects={projects}
        onProjectsChanged={refreshProjects}
        onNewTaskWithProject={async (project) => {
          const revision = ++navigationRevision.current;
          const task = await createProjectTask(project.projectId, defaultAgent);
          if (navigationRevision.current !== revision) return;
          router.push(`/?thread=${encodeURIComponent(task.thread_id)}`);
        }}
        searchControl={
          <ProductivityCommandCenter
            agents={agents}
            onNewTask={goHome}
            onSelectTask={selectTask}
            onStartWithAgent={() => goHome()}
          />
        }
        activeNav={activeNav ?? routeWorkspace}
      />
      <div
        className="studio-unified-content"
        data-sidebar-collapsed={collapsed ? "true" : "false"}
        aria-hidden={compactProject && !collapsed ? true : undefined}
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
            <SidebarIcon name="panel" />
          </button>
        )}
        {children}
      </div>
    </main>
  );
}
