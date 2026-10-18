"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { useAuth } from "./auth-provider";
import {
  ProjectFilesDialog,
  ProjectKnowledgeDialog,
  ProjectMemoriesDialog,
  ProjectSkillsDialog,
} from "./project-resource-dialogs";
import { AssistantRuntimeShell } from "./assistant-runtime-shell";
import { AgentThread } from "./agent-thread";
import { useRunViewModel } from "../lib/activity-store";
import { notifyTaskListChanged } from "../lib/task-history";
import { createUserScopedStorage, createNewThread } from "../lib/thread-store";
import { loadTaskAgentCatalog, type TaskAgent } from "../lib/task-agent-catalog";
import { loadTaskModelRoutes, type TaskModelRoute } from "../lib/task-model-catalog";
import {
  projectClient,
  studioClient,
  type ApiProject,
  type ApiProjectDocument,
  type ApiProjectLink,
  type ApiProjectMemory,
  type StudioKnowledgeBase,
  type StudioPlatformSkillPackage,
} from "../lib/studio-client";
import styles from "./project-home.module.css";

function formatDate(value: string) {
  return new Intl.DateTimeFormat("zh-CN", {
    month: "long",
    day: "numeric",
  }).format(new Date(value));
}

function ProjectRunSync() {
  const runView = useRunViewModel();
  const syncedRun = useRef<string | null>(null);
  useEffect(() => {
    const runId = runView?.runId;
    if (!runId || runId === syncedRun.current) return;
    syncedRun.current = runId;
    // The task binding lands with the Run, so the sidebar can only group the
    // new task under its project once the Run is visible.
    notifyTaskListChanged();
  }, [runView?.runId]);
  return null;
}

function ProjectIcon() {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true">
      <path d="M3.5 7.2A2.7 2.7 0 0 1 6.2 4.5h3.4l2.2 2.4h5.9a2.7 2.7 0 0 1 2.7 2.7v6.7a2.7 2.7 0 0 1-2.7 2.7H6.2a2.7 2.7 0 0 1-2.7-2.7Z" />
      <path d="M7 12.3h10" />
    </svg>
  );
}

function ResourceCard({
  title,
  description,
  action,
  onAction,
  onExpand,
  count,
  children,
}: {
  title: string;
  description: string;
  action: string;
  onAction: () => void;
  onExpand: () => void;
  count?: number;
  children?: ReactNode;
}) {
  return (
    <section className={styles.card}>
      <div className={styles.cardHead}>
        <div className={styles.cardTitle}>
          <h2>{title}</h2>
          {typeof count === "number" && count > 0 ? <span className={styles.count}>{count}</span> : null}
        </div>
        <button
          type="button"
          className={styles.expand}
          aria-label={`展开${title}`}
          title={`展开${title}`}
          onClick={onExpand}
        >
          <svg viewBox="0 0 16 16" aria-hidden="true">
            <path d="M6 3h7v7M13 3 8 8M4 5h2M4 8v3h3" />
          </svg>
        </button>
      </div>
      <p className={styles.cardDescription}>{description}</p>
      {children}
      <button type="button" className={styles.cardAction} onClick={onAction}>
        <span aria-hidden="true">＋</span>
        {action}
      </button>
    </section>
  );
}

export function ProjectHome({ projectId }: { projectId: string }) {
  const { user } = useAuth();
  const [project, setProject] = useState<ApiProject | null>(null);
  const [memories, setMemories] = useState<ApiProjectMemory[]>([]);
  const [documents, setDocuments] = useState<ApiProjectDocument[]>([]);
  const [links, setLinks] = useState<ApiProjectLink[]>([]);
  const [knowledgeBases, setKnowledgeBases] = useState<StudioKnowledgeBase[]>([]);
  const [skillCatalog, setSkillCatalog] = useState<StudioPlatformSkillPackage[]>([]);
  const [defaultAgent, setDefaultAgent] = useState<TaskAgent | null>(null);
  const [modelRoutes, setModelRoutes] = useState<TaskModelRoute[]>([]);
  const [modelRouteOverride, setModelRouteOverride] = useState<string | null>(null);
  const [threadId, setThreadId] = useState<string | null>(null);
  const [threadPersisted, setThreadPersisted] = useState(false);
  const [loading, setLoading] = useState(true);
  const [activeDialog, setActiveDialog] = useState<
    "knowledge" | "files" | "skills" | "memories" | null
  >(null);
  const [error, setError] = useState("");

  const reload = useCallback(async () => {
    setError("");
    try {
      const [resources, skillCatalogValue, knowledgeCatalog] = await Promise.all([
        projectClient.resources(projectId),
        studioClient.listPlatformSkills().catch(() => ({ revision: 0, packages: [] })),
        studioClient.listKnowledgeBases().catch(() => []),
      ]);
      setProject(resources.project);
      setMemories(resources.memories);
      setDocuments(resources.documents);
      setLinks(resources.links ?? []);
      setSkillCatalog(skillCatalogValue.packages);
      setKnowledgeBases(knowledgeCatalog);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "读取项目失败，请稍后重试。");
    } finally {
      setLoading(false);
    }
  }, [projectId]);

  useEffect(() => {
    void reload();
  }, [reload]);

  useEffect(() => {
    setThreadId(createNewThread(createUserScopedStorage(window.localStorage, user.user_id)));
  }, [user.user_id]);

  useEffect(() => {
    let active = true;
    loadTaskAgentCatalog(user.user_id, false)
      .then((catalog) => { if (active) setDefaultAgent(catalog.defaultAgent); })
      .catch(() => { /* The composer surfaces the failure on send. */ });
    loadTaskModelRoutes()
      .then((routes) => { if (active) setModelRoutes(routes); })
      .catch(() => { /* TaskModelControl falls back to the agent default. */ });
    return () => { active = false; };
  }, [user.user_id]);

  const handleComposerSubmitted = useCallback(() => {
    setThreadPersisted(true);
    notifyTaskListChanged();
  }, []);

  const skills = useMemo(() => {
    const byReference = new Map(
      skillCatalog.map((pkg) => [`${pkg.packageId}@${pkg.revision}`, pkg]),
    );
    return (project?.skillReferences ?? []).map((reference) => ({
      reference,
      package: byReference.get(reference),
    }));
  }, [project?.skillReferences, skillCatalog]);

  const knowledge = useMemo(() => {
    const byReference = new Map(knowledgeBases.map((item) => [item.reference, item]));
    return (project?.knowledgeReferences ?? []).map((reference) => ({
      reference,
      base: byReference.get(reference),
    }));
  }, [project?.knowledgeReferences, knowledgeBases]);

  if (loading) {
    return (
      <div className={styles.page} aria-busy="true">
        <div className={styles.loading} role="status">正在读取项目…</div>
      </div>
    );
  }

  if (!project) {
    return (
      <div className={styles.page}>
        <div className={styles.emptyPage}>
          <h1>项目不可用</h1>
          <p>{error || "这个项目可能已被移除，或者你没有访问权限。"}</p>
          <Link href="/studio/agents">返回智能体</Link>
        </div>
      </div>
    );
  }

  return (
    <div className={styles.page} data-project-home="true">
      <header className={styles.header}>
        <div className={styles.identity}>
          <span className={styles.projectIcon} aria-hidden="true"><ProjectIcon /></span>
          <div className={styles.identityCopy}>
            <h1>{project.name}</h1>
            <p>
              {project.userId === user.user_id ? "由你创建" : "由协作者创建"}
              {" · "}
              更新于 {formatDate(project.updatedAt)}
            </p>
          </div>
        </div>
      </header>

      {error && <p className={styles.error} role="alert">{error}</p>}

      <div className={styles.layout}>
        <main className={styles.main} aria-label="项目任务">
          {threadId && defaultAgent ? (
            <div className={styles.threadHost}>
              <AssistantRuntimeShell
                key={threadId}
                threadId={threadId}
                localOnly={!threadPersisted}
                projectId={projectId}
                agentName={defaultAgent.name}
                agentVersion={defaultAgent.version}
                agentOwnerUserId={defaultAgent.ownerUserId}
                spaceId={defaultAgent.spaceId}
                agentDefaultModelRoute={defaultAgent.modelRoute ?? null}
                modelRoutes={modelRoutes}
                modelRouteOverride={modelRouteOverride}
                onModelRouteOverrideChange={setModelRouteOverride}
              >
                <ProjectRunSync />
                <AgentThread
                  userId={user.user_id}
                  threadId={threadId}
                  agents={[defaultAgent]}
                  selectedAgent={defaultAgent}
                  agentsLoading={false}
                  currentTaskBusy={false}
                  onComposerSubmitted={handleComposerSubmitted}
                />
              </AssistantRuntimeShell>
            </div>
          ) : (
            <div className={styles.loading} role="status">正在准备项目输入…</div>
          )}
        </main>

        <aside className={styles.rail} aria-label="项目资源">
          <ResourceCard
            title="知识库"
            description="绑定知识库，让项目里的每个任务都能检索同一批资料。"
            action={knowledge.length ? "配置" : "添加"}
            count={knowledge.length}
            onAction={() => setActiveDialog("knowledge")}
            onExpand={() => setActiveDialog("knowledge")}
          >
            {knowledge.length ? (
              <ul className={styles.chips}>
                {knowledge.slice(0, 6).map(({ reference, base }) => (
                  <li key={reference} title={reference}>{base?.displayName ?? reference}</li>
                ))}
              </ul>
            ) : <p className={styles.muted}>还没有绑定知识库。</p>}
          </ResourceCard>

          <ResourceCard
            title="文件和资源"
            description="附加文件和网页，供项目的所有任务使用。"
            action="添加"
            count={documents.length + links.length}
            onAction={() => setActiveDialog("files")}
            onExpand={() => setActiveDialog("files")}
          >
            {documents.length + links.length ? (
              <ul className={styles.fileList}>
                {documents.slice(0, 3).map((document) => (
                  <li key={document.inputArtifactId}>
                    <span className={styles.fileGlyph} aria-hidden="true">▤</span>
                    <span className={styles.fileName} title={document.name}>{document.name}</span>
                    <a
                      href={`/api/input-artifacts/${encodeURIComponent(document.inputArtifactId)}/content?inline=1`}
                      target="_blank"
                      rel="noreferrer"
                      onClick={(event) => event.stopPropagation()}
                    >
                      查看
                    </a>
                  </li>
                ))}
                {links.slice(0, 3).map((link) => (
                  <li key={link.linkId}>
                    <span className={styles.fileGlyph} aria-hidden="true">↗</span>
                    <span className={styles.fileName} title={link.url}>{link.title || link.url}</span>
                    <a
                      href={link.url}
                      target="_blank"
                      rel="noreferrer"
                      onClick={(event) => event.stopPropagation()}
                    >
                      打开
                    </a>
                  </li>
                ))}
                {documents.length + links.length > 6 && (
                  <li className={styles.moreLine}>还有 {documents.length + links.length - 6} 项</li>
                )}
              </ul>
            ) : <p className={styles.muted}>还没有附加文件或网页。</p>}
          </ResourceCard>

          <ResourceCard
            title="技能"
            description="在相关情况下跨任务复用已经跑通的工作流。"
            action="添加"
            count={skills.length}
            onAction={() => setActiveDialog("skills")}
            onExpand={() => setActiveDialog("skills")}
          >
            {skills.length ? (
              <ul className={styles.chips}>
                {skills.slice(0, 6).map(({ reference, package: pkg }) => (
                  <li key={reference} title={reference}>{pkg?.displayName ?? reference}</li>
                ))}
              </ul>
            ) : <p className={styles.muted}>还没有项目技能。</p>}
          </ResourceCard>

          <ResourceCard
            title="记忆"
            description="长期有效的事实和决定，会随项目带给每个新任务。"
            action="添加"
            count={memories.length}
            onAction={() => setActiveDialog("memories")}
            onExpand={() => setActiveDialog("memories")}
          >
            {memories.length ? (
              <ul className={styles.memoryList}>
                {memories.slice(0, 3).map((memory) => <li key={memory.memoryId}>{memory.content}</li>)}
                {memories.length > 3 && <li className={styles.moreLine}>还有 {memories.length - 3} 条记忆</li>}
              </ul>
            ) : <p className={styles.muted}>还没有项目记忆。</p>}
          </ResourceCard>
        </aside>
      </div>

      {activeDialog === "knowledge" && (
        <ProjectKnowledgeDialog
          project={project}
          onClose={() => setActiveDialog(null)}
          onChanged={() => void reload()}
        />
      )}
      {activeDialog === "files" && (
        <ProjectFilesDialog
          project={project}
          mode="manage"
          onClose={() => setActiveDialog(null)}
          onChanged={() => void reload()}
        />
      )}
      {activeDialog === "skills" && (
        <ProjectSkillsDialog
          project={project}
          packages={skillCatalog}
          onClose={() => setActiveDialog(null)}
          onChanged={() => void reload()}
        />
      )}
      {activeDialog === "memories" && (
        <ProjectMemoriesDialog
          project={project}
          onClose={() => setActiveDialog(null)}
          onChanged={() => void reload()}
        />
      )}
    </div>
  );
}
