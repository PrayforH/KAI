"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useMemo, useState, type ReactNode } from "react";
import { useAuth } from "./auth-provider";
import { ProjectSettingsDialog } from "./project-settings-dialog";
import { AssistantRuntimeShell } from "./assistant-runtime-shell";
import { AgentThread } from "./agent-thread";
import { notifyTaskListChanged } from "../lib/task-history";
import { createUserScopedStorage, createNewThread } from "../lib/thread-store";
import { loadTaskAgentCatalog, type TaskAgent } from "../lib/task-agent-catalog";
import { loadTaskModelRoutes, type TaskModelRoute } from "../lib/task-model-catalog";
import {
  projectClient,
  studioClient,
  type ApiProject,
  type ApiProjectDocument,
  type ApiProjectMemory,
  type StudioPlatformSkillPackage,
} from "../lib/studio-client";
import styles from "./project-home.module.css";

function formatDate(value: string) {
  return new Intl.DateTimeFormat("zh-CN", {
    month: "long",
    day: "numeric",
  }).format(new Date(value));
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
  count,
  children,
}: {
  title: string;
  description: string;
  action: string;
  onAction: () => void;
  count?: number;
  children?: ReactNode;
}) {
  return (
    <section className={styles.card}>
      <div className={styles.cardHead}>
        <h2>{title}</h2>
        {typeof count === "number" && count > 0 ? <span className={styles.count}>{count}</span> : null}
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
  const router = useRouter();
  const { user } = useAuth();
  const [project, setProject] = useState<ApiProject | null>(null);
  const [memories, setMemories] = useState<ApiProjectMemory[]>([]);
  const [documents, setDocuments] = useState<ApiProjectDocument[]>([]);
  const [skillCatalog, setSkillCatalog] = useState<StudioPlatformSkillPackage[]>([]);
  const [defaultAgent, setDefaultAgent] = useState<TaskAgent | null>(null);
  const [modelRoutes, setModelRoutes] = useState<TaskModelRoute[]>([]);
  const [modelRouteOverride, setModelRouteOverride] = useState<string | null>(null);
  const [threadId, setThreadId] = useState<string | null>(null);
  const [threadPersisted, setThreadPersisted] = useState(false);
  const [loading, setLoading] = useState(true);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [error, setError] = useState("");

  const reload = useCallback(async () => {
    setError("");
    try {
      const [resources, skillCatalogValue] = await Promise.all([
        projectClient.resources(projectId),
        studioClient.listPlatformSkills().catch(() => ({ revision: 0, packages: [] })),
      ]);
      setProject(resources.project);
      setMemories(resources.memories);
      setDocuments(resources.documents);
      setSkillCatalog(skillCatalogValue.packages);
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
    <div className={styles.page}>
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
        <button type="button" className={styles.settingsButton} onClick={() => setSettingsOpen(true)}>
          项目设置
        </button>
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
            title="指令"
            description="指导项目中的智能体如何响应该项目的所有任务。"
            action={project.instructions?.trim() ? "编辑" : "添加"}
            onAction={() => setSettingsOpen(true)}
          >
            {project.instructions?.trim()
              ? <p className={styles.previewText}>{project.instructions}</p>
              : <p className={styles.muted}>还没有项目指令。</p>}
          </ResourceCard>

          <ResourceCard
            title="文件和资源"
            description="附加文件和资料，供项目的所有任务使用。"
            action="上传"
            count={documents.length}
            onAction={() => setSettingsOpen(true)}
          >
            {documents.length ? (
              <ul className={styles.fileList}>
                {documents.slice(0, 4).map((document) => (
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
                {documents.length > 4 && <li className={styles.moreLine}>还有 {documents.length - 4} 个文件</li>}
              </ul>
            ) : <p className={styles.muted}>还没有附加文件。</p>}
          </ResourceCard>

          <ResourceCard
            title="技能"
            description="在相关情况下跨任务复用已经跑通的工作流。"
            action="添加"
            count={skills.length}
            onAction={() => setSettingsOpen(true)}
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
            onAction={() => setSettingsOpen(true)}
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

      {settingsOpen && (
        <ProjectSettingsDialog
          project={project}
          onClose={() => { setSettingsOpen(false); void reload(); }}
          onChanged={() => { void reload(); }}
          onRemoved={() => router.push("/")}
        />
      )}
    </div>
  );
}
