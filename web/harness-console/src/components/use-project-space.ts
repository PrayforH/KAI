"use client";

import { useRouter, useSearchParams } from "next/navigation";
import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type FormEvent,
} from "react";
import { useAuth } from "./auth-provider";
import { useConfirmationDialog } from "./confirmation-dialog";
import { startProjectTaskTransition } from "../lib/project-task-transition";
import { armProjectTaskLaunch } from "../lib/project-task-launch";
import { createUserScopedStorage } from "../lib/thread-store";
import { saveTaskModelOverride } from "../lib/task-model-catalog";
import { createProjectTask } from "../lib/project-task";
import {
  loadTaskAgentCatalog,
  chatUsableAgents,
  agentItemKey,
  type TaskAgent,
} from "../lib/task-agent-catalog";
import { notifyTaskListChanged, type TaskSummary } from "../lib/task-history";
import {
  loadProjectArtifacts,
  loadProjectTasks,
  projectHref,
  projectTab,
  type ProjectArtifact,
  type ProjectTab,
} from "../lib/project-space";
import {
  projectClient,
  studioClient,
  type ApiProjectResources,
  type ApiProjectResult,
  type StudioKnowledgeBase,
  type StudioPlatformSkillPackage,
} from "../lib/studio-client";
import { ACTIVE, PAGE_SIZE, errorText } from "./project-space-shared";

export function useProjectSpace(
  projectId: string,
  initialTab: ProjectTab = "overview",
) {
  const { user } = useAuth();
  const router = useRouter();
  useEffect(() => { router.prefetch("/"); }, [router]);
  const searchParams = useSearchParams();
  const urlTab = searchParams.get("tab");
  const urlSettings = searchParams.get("settings");
  const [tab, setTab] = useState(initialTab);
  useEffect(() => setTab(projectTab(urlTab)), [urlTab]);
  const [resources, setResources] = useState<ApiProjectResources | null>(null);
  const [tasks, setTasks] = useState<TaskSummary[]>([]);
  const [savedResults, setSavedResults] = useState<ApiProjectResult[]>([]);
  const [savedResultError, setSavedResultError] = useState("");
  const [draft, setDraft] = useState("");
  const [artifacts, setArtifacts] = useState<ProjectArtifact[]>([]);
  const [agents, setAgents] = useState<TaskAgent[]>([]);
  const [selectedAgent, setSelectedAgent] = useState<TaskAgent | null>(null);
  const [packages, setPackages] = useState<StudioPlatformSkillPackage[]>([]);
  const [bases, setBases] = useState<StudioKnowledgeBase[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [taskError, setTaskError] = useState("");
  const [resultError, setResultError] = useState("");
  const [notice, setNotice] = useState("");
  const [creating, setCreating] = useState(false);
  const [query, setQuery] = useState("");
  const [status, setStatus] = useState("all");
  const [agentFilter, setAgentFilter] = useState("all");
  const [includeArchived, setIncludeArchived] = useState(false);
  const [more, setMore] = useState({ active: false, archived: false });
  const [loadingMore, setLoadingMore] = useState(false);
  const [dialog, setDialog] = useState<
    "files" | "knowledge" | "skills" | "connectors" | "instructions" | null
  >(null);
  const [settingsOpen, setSettingsOpenState] = useState(urlSettings === "1");
  useEffect(() => { setSettingsOpenState(urlSettings === "1"); }, [urlSettings]);
  function setSettingsOpen(open: boolean) {
    setSettingsOpenState(open);
    if (!open && new URLSearchParams(window.location.search).has("settings")) {
      const url = new URL(window.location.href);
      url.searchParams.delete("settings");
      window.history.replaceState(null, "", `${url.pathname}${url.search}${url.hash}`);
    }
  }
  const [name, setName] = useState("");
  const [instructions, setInstructions] = useState("");
  const [saving, setSaving] = useState(false);
  const [memory, setMemory] = useState("");
  const [editingMemory, setEditingMemory] = useState<string | null>(null);
  const [memoryPending, setMemoryPending] = useState(false);
  const epoch = useRef(0);
  const createLock = useRef(false);
  const { requestConfirmation, confirmationDialog } = useConfirmationDialog();
  const preferenceKey = `harness:project-agent:${user.user_id}:${projectId}`;

  const reload = useCallback(async () => {
    const revision = ++epoch.current;
    setError("");
    const results = await Promise.allSettled([
      projectClient.resources(projectId),
      loadProjectTasks(projectId),
      loadProjectTasks(projectId, true),
      loadProjectArtifacts(projectId),
      loadTaskAgentCatalog(user.user_id, false),
      studioClient.listPlatformSkills(),
      studioClient.listKnowledgeBases(),
      projectClient.results(projectId),
    ] as const);
    if (epoch.current !== revision) return;
    const [
      resourceResult,
      activeResult,
      archivedResult,
      artifactResult,
      catalogResult,
      skillResult,
      baseResult,
      savedResult,
    ] = results;
    if (resourceResult.status === "fulfilled") {
      setResources(resourceResult.value);
      setName(resourceResult.value.project.name);
      setInstructions(resourceResult.value.project.instructions ?? "");
    } else setError(errorText(resourceResult.reason));
    setTaskError(
      activeResult.status === "rejected" || archivedResult.status === "rejected"
        ? "部分任务未能读取，请刷新重试。"
        : "",
    );
    const activeTasks =
      activeResult.status === "fulfilled" ? activeResult.value : [];
    const archivedTasks =
      archivedResult.status === "fulfilled" ? archivedResult.value : [];
    setTasks([...activeTasks, ...archivedTasks]);
    setMore({
      active: activeTasks.length === PAGE_SIZE,
      archived: archivedTasks.length === PAGE_SIZE,
    });
    if (artifactResult.status === "fulfilled") {
      setArtifacts(artifactResult.value);
      setResultError("");
    } else setResultError(errorText(artifactResult.reason));
    if (catalogResult.status === "fulfilled") {
      const usable = chatUsableAgents(catalogResult.value.agents);
      setAgents(usable);
      let remembered: string | null = null;
      try {
        remembered = localStorage.getItem(preferenceKey);
      } catch {
        /* preferences are optional */
      }
      setSelectedAgent(
        (current) =>
          usable.find(
            (agent) =>
              agentItemKey(agent) ===
              (current ? agentItemKey(current) : remembered),
          ) ??
          usable.find(
            (agent) =>
              agentItemKey(agent) ===
              agentItemKey(catalogResult.value.defaultAgent),
          ) ??
          usable[0] ??
          null,
      );
    } else
      setNotice("智能体目录暂时无法读取，可以先整理项目资料，再刷新重试。");
    if (skillResult.status === "fulfilled")
      setPackages(skillResult.value.packages);
    if (baseResult.status === "fulfilled") setBases(baseResult.value);
    if (savedResult.status === "fulfilled") {
      setSavedResults(savedResult.value);
      setSavedResultError("");
    } else setSavedResultError(errorText(savedResult.reason));
    setLoading(false);
  }, [projectId, user.user_id, preferenceKey]);

  useEffect(() => {
    void reload();
    return () => {
      epoch.current += 1;
    };
  }, [reload]);

  async function refreshResources() {
    try {
      const next = await projectClient.resources(projectId);
      setResources(next);
      return next;
    } catch (cause) {
      setError(errorText(cause));
    }
  }
  useEffect(() => {
    const changed = (event: Event) => { if ((event as CustomEvent<{projectId:string}>).detail?.projectId === projectId) void refreshResources(); };
    window.addEventListener("harness:project-results-changed", changed);
    return () => window.removeEventListener("harness:project-results-changed", changed);
  }, [projectId]);
  function chooseTab(next: ProjectTab) {
    setTab(next);
    setQuery("");
    if (
      next === "overview" &&
      new URLSearchParams(window.location.search).has("tab")
    ) {
      window.history.replaceState(
        null,
        "",
        projectHref(projectId),
      );
    }
  }
  async function startTask(prompt = "", modelRoute: string | null = null) {
    if (createLock.current || !selectedAgent) return;
    createLock.current = true;
    setCreating(true);
    setError("");
    let navigating = false;
    try {
      const task = await createProjectTask(projectId, selectedAgent);
      try {
        localStorage.setItem(preferenceKey, agentItemKey(selectedAgent));
        saveTaskModelOverride(createUserScopedStorage(localStorage, user.user_id), task.thread_id, modelRoute);
      } catch {
        /* optional preference */
      }
      if (prompt.trim())
        armProjectTaskLaunch(user.user_id, task.thread_id, prompt.trim());
      navigating = true;
      startProjectTaskTransition(task.thread_id, () => router.push(`/?thread=${encodeURIComponent(task.thread_id)}`));
    } catch (cause) {
      setError(errorText(cause));
    } finally {
      if (!navigating) { createLock.current = false; setCreating(false); }
    }
  }
  async function loadMore() {
    if (loadingMore) return;
    setLoadingMore(true);
    const revision = epoch.current;
    try {
      const archived = !more.active;
      const offset = tasks.filter(
        (task) => Boolean(task.archived_at) === archived,
      ).length;
      const next = await loadProjectTasks(projectId, archived, offset);
      if (epoch.current !== revision) return;
      setTasks((current) => [
        ...current,
        ...next.filter(
          (task) => !current.some((item) => item.thread_id === task.thread_id),
        ),
      ]);
      setMore((current) => ({
        ...current,
        [archived ? "archived" : "active"]: next.length === PAGE_SIZE,
      }));
      setTaskError("");
    } catch (cause) {
      setTaskError(errorText(cause));
    } finally {
      setLoadingMore(false);
    }
  }
  async function saveSettings(event: FormEvent, instructionsOnly = false) {
    event.preventDefault();
    if (saving) return;
    setSaving(true);
    setError("");
    try {
      const updated = await projectClient.update(projectId, {
        ...(instructionsOnly ? {} : { name: name.trim() }),
        instructions,
      });
      setResources((current) =>
        current ? { ...current, project: updated } : current,
      );
      window.dispatchEvent(new CustomEvent("harness:projects-changed"));
      notifyTaskListChanged();
      setSettingsOpen(false);
      setDialog((current) => (current === "instructions" ? null : current));
      setNotice("项目设置已保存，后续任务将使用新的项目指令。");
    } catch (cause) {
      setError(errorText(cause));
    } finally {
      setSaving(false);
    }
  }
  async function saveMemory(event: FormEvent) {
    event.preventDefault();
    if (memoryPending || !memory.trim()) return;
    setMemoryPending(true);
    setError("");
    try {
      if (editingMemory)
        await projectClient.updateMemory(
          projectId,
          editingMemory,
          memory.trim(),
        );
      else await projectClient.addMemory(projectId, memory.trim());
      setMemory("");
      setEditingMemory(null);
      await refreshResources();
    } catch (cause) {
      setError(errorText(cause));
    } finally {
      setMemoryPending(false);
    }
  }
  async function removeMemory(id: string) {
    if (memoryPending) return;
    const confirmed = await requestConfirmation({
      title: "移除这条项目事实？",
      description: "后续任务将不再带入这条事实，已执行的任务保留原上下文。",
      confirmLabel: "移除",
      tone: "danger",
    });
    if (!confirmed) return;
    setMemoryPending(true);
    try {
      await projectClient.removeMemory(projectId, id);
      await refreshResources();
    } catch (cause) {
      setError(errorText(cause));
    } finally {
      setMemoryPending(false);
    }
  }
  async function archiveProject() {
    const confirmed = await requestConfirmation({
      title: "归档这个项目？",
      description: "保留资料与任务历史，归档后不能新建项目任务。",
      confirmLabel: "归档",
    });
    if (!confirmed) return;
    try {
      await projectClient.archive(projectId);
      await refreshResources();
      window.dispatchEvent(new CustomEvent("harness:projects-changed"));
      notifyTaskListChanged();
      setSettingsOpen(false);
    } catch (cause) {
      setError(errorText(cause));
    }
  }
  async function restoreProject() {
    if (saving) return;
    setSaving(true);
    try {
      const updated = await projectClient.update(projectId, {
        archived: false,
      });
      setResources((current) =>
        current ? { ...current, project: updated } : current,
      );
      window.dispatchEvent(new CustomEvent("harness:projects-changed"));
      notifyTaskListChanged();
      setSettingsOpen(false);
      setNotice("项目已恢复，可以继续新建任务。");
    } catch (cause) {
      setError(errorText(cause));
    } finally {
      setSaving(false);
    }
  }

  const project = resources?.project;
  const activeTasks = tasks.filter((task) => !task.archived_at);
  const visibleTasks = tasks.filter(
    (task) =>
      (includeArchived || !task.archived_at) &&
      (status === "all" ||
        (status === "active"
          ? ACTIVE.has(task.status)
          : status === "attention"
            ? Boolean(
                task.pending_approval ||
                  task.pending_input ||
                  ["failed", "timed_out", "rejected"].includes(task.status),
              )
            : task.status === status)) &&
      (agentFilter === "all" ||
        `${task.agent_owner_user_id}:${task.space_id ?? ""}:${task.agent_name}` ===
          agentFilter) &&
      [task.title, task.agent_name].some((value) =>
        value.toLowerCase().includes(query.toLowerCase()),
      ),
  );
  const recent = [...activeTasks]
    .sort((a, b) => Number(Boolean(b.pinned_at)) - Number(Boolean(a.pinned_at)) || b.updated_at.localeCompare(a.updated_at))
    .slice(0, 5);
  const visibleArtifacts = artifacts.filter((artifact) =>
    [artifact.name, artifact.thread_title].some((value) =>
      value.toLowerCase().includes(query.toLowerCase()),
    ),
  );
  const hasMore = more.active || (includeArchived && more.archived);
  const agentLabels = new Map(
    agents.map((agent) => [
      `${agent.ownerUserId}:${agent.spaceId ?? ""}:${agent.name}`,
      agent.displayName,
    ]),
  );
  const knowledgeNames = new Map(
    bases.map((base) => [base.reference, base.displayName]),
  );
  const skillNames = new Map(
    packages.map((pkg) => [
      `${pkg.packageId}@${pkg.revision}`,
      pkg.displayName,
    ]),
  );
  const taskAgentNames = new Map(
    tasks.map((task) => [
      `${task.agent_owner_user_id}:${task.space_id ?? ""}:${task.agent_name}`,
      agentLabels.get(
        `${task.agent_owner_user_id}:${task.space_id ?? ""}:${task.agent_name}`,
      ) ?? task.agent_name,
    ]),
  );

  return {
    savedResults,
    setSavedResults,
    savedResultError,
    draft,
    setDraft,
    tab,
    resources,
    tasks,
    artifacts,
    agents,
    selectedAgent,
    setSelectedAgent,
    packages,
    loading,
    setLoading,
    error,
    setError,
    taskError,
    resultError,
    notice,
    setNotice,
    creating,
    query,
    setQuery,
    status,
    setStatus,
    agentFilter,
    setAgentFilter,
    includeArchived,
    setIncludeArchived,
    more,
    loadingMore,
    dialog,
    setDialog,
    settingsOpen,
    setSettingsOpen,
    name,
    setName,
    instructions,
    setInstructions,
    saving,
    memory,
    setMemory,
    editingMemory,
    setEditingMemory,
    memoryPending,
    confirmationDialog,
    reload,
    refreshResources,
    chooseTab,
    startTask,
    loadMore,
    saveSettings,
    saveMemory,
    removeMemory,
    archiveProject,
    restoreProject,
    project,
    activeTasks,
    visibleTasks,
    recent,
    visibleArtifacts,
    hasMore,
    agentLabels,
    knowledgeNames,
    skillNames,
    taskAgentNames,
  };
}
export type ProjectSpaceState = ReturnType<typeof useProjectSpace>;
