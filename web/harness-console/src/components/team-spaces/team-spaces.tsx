"use client";

import Link from "next/link";
import { type FormEvent, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { spaceMemberDirectory } from "../../lib/team-space-members";
import { useDialogFocus } from "../../lib/use-dialog-focus";
import { useAuth } from "../auth-provider";
import { SecretInput } from "../secret-input";
import { StudioSidebar } from "../agent-studio/studio-sidebar";
import styles from "./team-spaces.module.css";

type SpaceRole = "owner" | "admin" | "contributor" | "viewer";
type TeamSpace = {
  tenantId: string;
  spaceId: string;
  name: string;
  description: string;
  createdBy: string;
  createdAt: string;
};
type SpaceMember = {
  tenantId: string;
  spaceId: string;
  userId: string;
  role: SpaceRole;
  createdAt: string;
};
type SpaceSummary = { space: TeamSpace; membership: SpaceMember };
type SpaceWorkspace = {
  summary: SpaceSummary;
  members: SpaceMember[];
  directory: TenantMember[];
  agents: AgentPermissions[];
  releases_by_agent: Record<string, ReleaseItem[]>;
  acls_by_agent: Record<string, AgentAcl[]>;
  knowledge: SharedKnowledge[];
  mcp_credentials: McpCredentialStatus[];
};
type TenantMember = {
  user: { user_id: string; display_name: string; email: string };
  membership: { role: string };
};
type CatalogAgent = {
  name: string;
  version: string;
  display_name: string;
  domain: string;
  owner_user_id: string;
  scope: "personal" | "team";
  mcp_references: string[];
  knowledge_references: string[];
};
type McpCredentialStatus = { reference: string; configured: boolean; keyNames: string[] };
type McpCapability = {
  reference: string;
  label: string;
  enabled: boolean;
  authMode: "none" | "bearer" | "header" | "query";
  authKey: string;
};
type StudioCatalogRecord = { catalog: { mcpServers: McpCapability[] } };
type WorkspaceAgent = {
  tenantId: string;
  agentId: string;
  scope: "personal" | "workspace";
  ownerUserId: string | null;
  spaceId: string | null;
  name: string;
  displayName: string;
  description: string;
  status: "active" | "archived";
  currentVersion: string | null;
  createdBy: string;
  createdAt: string;
  updatedAt: string;
};
type AgentPermissions = {
  agent: WorkspaceAgent;
  permissions: string[];
  can_view: boolean;
  can_chat: boolean;
  can_edit: boolean;
  can_publish: boolean;
  can_manage: boolean;
};
type ReleaseItem = {
  release: {
    tenantId: string;
    spaceId: string;
    agentId: string;
    version: string;
    sourceOwnerUserId: string;
    sourceName: string;
    promotedBy: string;
    runnableByViewer: boolean;
    connectionMode: "caller_owned" | "service_owned";
    createdAt: string;
  };
  agent: CatalogAgent & {
    agent_id?: string | null;
    current_version?: string | null;
    can_view?: boolean;
    can_chat?: boolean;
  };
};
type AgentAcl = {
  tenantId: string;
  agentId: string;
  granteeType: "user" | "group" | "space_role";
  granteeId: string;
  permission: string;
  grantedBy: string;
  createdAt: string;
};
type KnowledgeBase = { reference: string; displayName: string; description: string };
type SharedKnowledge = {
  spaceId: string;
  knowledgeBaseReference: string;
  sharedBy: string;
  createdAt: string;
};
type SpaceView = "agents" | "knowledge" | "members";

const ROLE_LABELS: Record<SpaceRole, string> = {
  owner: "所有者",
  admin: "管理员",
  contributor: "贡献者",
  viewer: "查看者",
};

const PERMISSION_LABELS: Record<string, string> = {
  view: "查看",
  chat: "对话",
  edit: "编辑",
  publish: "发布",
  manage: "管理",
};

async function request<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, { cache: "no-store", ...init });
  if (!response.ok) {
    const payload = (await response.json().catch(() => null)) as
      | { error?: { message?: string } }
      | null;
    throw new Error(payload?.error?.message || `HTTP ${response.status}`);
  }
  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
}

function agentKey(agent: Pick<CatalogAgent, "owner_user_id" | "name" | "version">) {
  return `${agent.owner_user_id}:${agent.name}@${agent.version}`;
}

export function TeamSpaces() {
  const { user } = useAuth();
  const [spaces, setSpaces] = useState<SpaceSummary[]>([]);
  const [selectedId, setSelectedId] = useState("");
  const [members, setMembers] = useState<SpaceMember[]>([]);
  const [directory, setDirectory] = useState<TenantMember[]>([]);
  const [personalAgents, setPersonalAgents] = useState<CatalogAgent[]>([]);
  const [workspaceAgents, setWorkspaceAgents] = useState<AgentPermissions[]>([]);
  const [releasesByAgent, setReleasesByAgent] = useState<Record<string, ReleaseItem[]>>({});
  const [aclsByAgent, setAclsByAgent] = useState<Record<string, AgentAcl[]>>({});
  const [knowledgeBases, setKnowledgeBases] = useState<KnowledgeBase[]>([]);
  const [sharedKnowledge, setSharedKnowledge] = useState<SharedKnowledge[]>([]);
  const [mcpCatalog, setMcpCatalog] = useState<McpCapability[]>([]);
  const [spaceMcpCredentials, setSpaceMcpCredentials] = useState<McpCredentialStatus[]>([]);
  const [spaceName, setSpaceName] = useState("");
  const [spaceDescription, setSpaceDescription] = useState("");
  const [memberUserId, setMemberUserId] = useState("");
  const [memberRole, setMemberRole] = useState<SpaceRole>("contributor");
  const [agentSelection, setAgentSelection] = useState("");
  const [viewerRunnable, setViewerRunnable] = useState(true);
  const [connectionMode, setConnectionMode] = useState<"caller_owned" | "service_owned">("caller_owned");
  const [knowledgeDependencies, setKnowledgeDependencies] = useState<string[]>([]);
  const [mcpCredentialInputs, setMcpCredentialInputs] = useState<Record<string, string>>({});
  const [aclGrantee, setAclGrantee] = useState("");
  const [aclPermission, setAclPermission] = useState("chat");
  const [knowledgeSelection, setKnowledgeSelection] = useState("");
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [loading, setLoading] = useState(true);
  const [spaceLoading, setSpaceLoading] = useState(false);
  const [activeView, setActiveView] = useState<SpaceView>("members");
  const [showCreate, setShowCreate] = useState(false);
  const [createError, setCreateError] = useState("");
  const [confirmingMemberId, setConfirmingMemberId] = useState("");
  const [confirmingAction, setConfirmingAction] = useState("");
  const spaceLoadSequence = useRef(0);
  const createDialogRef = useRef<HTMLElement>(null);
  const createNameRef = useRef<HTMLInputElement>(null);

  const selected = spaces.find((item) => item.space.spaceId === selectedId) ?? null;
  const canManage = selected?.membership.role === "owner" || selected?.membership.role === "admin";
  const canShare = canManage || selected?.membership.role === "contributor";
  const memberById = useMemo(
    () => new Map(directory.map((item) => [item.user.user_id, item])),
    [directory],
  );
  const aclCandidates = useMemo(
    () => spaceMemberDirectory(directory, members),
    [directory, members],
  );
  const selectedAgent = useMemo(
    () => personalAgents.find((item) => agentKey(item) === agentSelection) ?? null,
    [agentSelection, personalAgents],
  );
  const mcpByReference = useMemo(
    () => new Map(mcpCatalog.map((item) => [item.reference, item])),
    [mcpCatalog],
  );
  const configuredSpaceMcp = useMemo(
    () => new Set(spaceMcpCredentials.filter((item) => item.configured).map((item) => item.reference)),
    [spaceMcpCredentials],
  );
  const sharedKnowledgeReferences = useMemo(
    () => new Set(sharedKnowledge.map((item) => item.knowledgeBaseReference)),
    [sharedKnowledge],
  );
  const accessibleKnowledgeReferences = useMemo(
    () => new Set(knowledgeBases.map((item) => item.reference)),
    [knowledgeBases],
  );
  const missingMcpDefinitions = (selectedAgent?.mcp_references ?? []).filter((reference) => {
    const capability = mcpByReference.get(reference);
    return !capability || !capability.enabled;
  });
  const missingServiceCredentials = (selectedAgent?.mcp_references ?? []).filter((reference) => {
    const capability = mcpByReference.get(reference);
    return capability?.authMode !== "none" && !configuredSpaceMcp.has(reference);
  });
  const missingKnowledgeAccess = (selectedAgent?.knowledge_references ?? []).filter(
    (reference) => !sharedKnowledgeReferences.has(reference) && !accessibleKnowledgeReferences.has(reference),
  );
  const unselectedKnowledge = (selectedAgent?.knowledge_references ?? []).filter(
    (reference) => !sharedKnowledgeReferences.has(reference) && !knowledgeDependencies.includes(reference),
  );
  const credentialsReady = connectionMode === "caller_owned" || missingServiceCredentials.every(
    (reference) => Boolean(mcpCredentialInputs[reference]?.trim()),
  );
  const dependenciesReady = missingMcpDefinitions.length === 0
    && missingKnowledgeAccess.length === 0
    && unselectedKnowledge.length === 0
    && credentialsReady
    && (canManage || (selectedAgent?.knowledge_references ?? []).every(
      (reference) => sharedKnowledgeReferences.has(reference),
    ));

  const closeCreate = useCallback(() => {
    if (busy !== "create") setShowCreate(false);
  }, [busy]);

  useDialogFocus({
    open: showCreate,
    panelRef: createDialogRef,
    initialFocusRef: createNameRef,
    onEscape: closeCreate,
  });

  const loadSpaces = useCallback(async () => {
    const values = await request<SpaceSummary[]>("/api/spaces");
    setSpaces(values);
    setSelectedId((current) =>
      values.some((item) => item.space.spaceId === current)
        ? current
        : values[0]?.space.spaceId ?? "",
    );
  }, []);

  const loadSpace = useCallback(async (spaceId: string) => {
    const sequence = ++spaceLoadSequence.current;
    setSpaceLoading(Boolean(spaceId));
    setMembers([]);
    setDirectory([]);
    setWorkspaceAgents([]);
    setReleasesByAgent({});
    setAclsByAgent({});
    setSharedKnowledge([]);
    setSpaceMcpCredentials([]);
    setConfirmingMemberId("");
    setConfirmingAction("");
    if (!spaceId) {
      setSpaceLoading(false);
      return;
    }
    try {
      const workspace = await request<SpaceWorkspace>(
        `/api/spaces/${encodeURIComponent(spaceId)}/workspace`,
      );
      if (sequence !== spaceLoadSequence.current) return;
      setMembers(workspace.members);
      setWorkspaceAgents(workspace.agents);
      setSharedKnowledge(workspace.knowledge);
      setSpaceMcpCredentials(workspace.mcp_credentials ?? []);
      setDirectory(workspace.directory);
      setReleasesByAgent(workspace.releases_by_agent);
      setAclsByAgent(workspace.acls_by_agent);
      const nextAclCandidates = spaceMemberDirectory(workspace.directory, workspace.members);
      setMemberUserId((current) =>
        workspace.directory.some((item) => item.user.user_id === current)
          ? current
          : workspace.directory[0]?.user.user_id ?? "",
      );
      setAclGrantee((current) =>
        nextAclCandidates.some((item) => item.user.user_id === current)
          ? current
          : nextAclCandidates[0]?.user.user_id ?? "",
      );
      setError("");
    } catch (caught) {
      if (sequence === spaceLoadSequence.current) {
        setError(caught instanceof Error ? caught.message : "空间内容暂不可用");
      }
    } finally {
      if (sequence === spaceLoadSequence.current) setSpaceLoading(false);
    }
  }, [spaces]);

  useEffect(() => {
    Promise.all([
      loadSpaces(),
      request<CatalogAgent[]>("/api/harness/agents").then((items) => {
        const personal = items.filter((item) => item.scope === "personal");
        setPersonalAgents(personal);
        setAgentSelection(personal[0] ? agentKey(personal[0]) : "");
        setKnowledgeDependencies(personal[0]?.knowledge_references ?? []);
      }),
      request<StudioCatalogRecord>("/api/studio/catalog").then((record) => {
        setMcpCatalog(record.catalog.mcpServers);
      }),
      request<KnowledgeBase[]>("/api/studio/knowledge/bases").then((items) => {
        setKnowledgeBases(items);
        setKnowledgeSelection(items[0]?.reference ?? "");
      }).catch(() => undefined),
    ])
      .catch((caught) => setError(caught instanceof Error ? caught.message : "共享空间暂不可用"))
      .finally(() => setLoading(false));
  }, [loadSpaces]);

  useEffect(() => {
    void loadSpace(selectedId);
  }, [loadSpace, selectedId]);

  async function createSpace(event: FormEvent) {
    event.preventDefault();
    setBusy("create"); setCreateError(""); setError(""); setNotice("");
    try {
      const created = await request<SpaceSummary>("/api/spaces", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: spaceName, description: spaceDescription }),
      });
      setSpaceName(""); setSpaceDescription("");
      await loadSpaces();
      setSelectedId(created.space.spaceId);
      setNotice(`已创建团队空间「${created.space.name}」。`);
      setShowCreate(false);
    } catch (caught) {
      setCreateError(caught instanceof Error ? caught.message : "创建失败");
    } finally { setBusy(""); }
  }

  async function addMember(event: FormEvent) {
    event.preventDefault();
    if (!selectedId || !memberUserId) return;
    setBusy("member"); setError(""); setNotice("");
    try {
      await request(`/api/spaces/${encodeURIComponent(selectedId)}/members`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ user_id: memberUserId, role: memberRole }),
      });
      await loadSpace(selectedId);
      setNotice("空间成员权限已保存；其私人任务仍不可见。");
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "成员保存失败");
    } finally { setBusy(""); }
  }

  async function removeMember(member: SpaceMember) {
    if (!selectedId || member.userId === user.user_id) return;
    setBusy(`member-remove:${member.userId}`); setError(""); setNotice("");
    try {
      await request(
        `/api/spaces/${encodeURIComponent(selectedId)}/members/${encodeURIComponent(member.userId)}`,
        { method: "DELETE" },
      );
      setConfirmingMemberId("");
      await loadSpace(selectedId);
      const profile = memberById.get(member.userId)?.user;
      setNotice(`已将${profile?.display_name ? `「${profile.display_name}」` : "该成员"}移出空间；其个人任务记录不受影响。`);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "成员移除失败");
    } finally { setBusy(""); }
  }

  async function shareAgent(event: FormEvent) {
    event.preventDefault();
    const agent = personalAgents.find((item) => agentKey(item) === agentSelection);
    if (!selectedId || !agent) return;
    setBusy("share"); setError(""); setNotice("");
    try {
      if (connectionMode === "service_owned") {
        await Promise.all(missingServiceCredentials.map((reference) => {
          const capability = mcpByReference.get(reference);
          return request(`/api/spaces/${encodeURIComponent(selectedId)}/mcp/${encodeURIComponent(reference)}/credentials`, {
            method: "PUT",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              authKey: capability?.authKey ?? "authorization",
              value: mcpCredentialInputs[reference]?.trim(),
            }),
          });
        }));
      }
      await request(`/api/spaces/${encodeURIComponent(selectedId)}/agents`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          owner_user_id: agent.owner_user_id,
          name: agent.name,
          version: agent.version,
          runnable_by_viewer: viewerRunnable,
          connection_mode: connectionMode,
          share_knowledge_references: knowledgeDependencies,
        }),
      });
      setMcpCredentialInputs({});
      await loadSpace(selectedId);
      setNotice(`${agent.display_name} ${agent.version} 已发布；MCP 采用${connectionMode === "service_owned" ? "空间共享凭据" : "成员个人凭据"}，知识库依赖已同步授权。`);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "发布失败");
    } finally { setBusy(""); }
  }

  async function promoteRelease(agentId: string, version: string) {
    if (!selectedId) return;
    setBusy(`promote:${agentId}`); setError(""); setNotice("");
    try {
      await request(
        `/api/spaces/${encodeURIComponent(selectedId)}/agents/${encodeURIComponent(agentId)}/releases/${encodeURIComponent(version)}/promote`,
        { method: "POST" },
      );
      await loadSpace(selectedId);
      setNotice(`当前发布版本已切换为 ${version}，Agent 身份不变。`);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "切换版本失败");
    } finally { setBusy(""); }
  }

  async function forkAgent(agentId: string) {
    if (!selectedId) return;
    setBusy(`fork:${agentId}`); setError(""); setNotice("");
    try {
      await request(
        `/api/spaces/${encodeURIComponent(selectedId)}/agents/${encodeURIComponent(agentId)}/fork`,
        { method: "POST" },
      );
      const agents = await request<CatalogAgent[]>("/api/harness/agents");
      setPersonalAgents(agents.filter((agent) => agent.scope === "personal"));
      setNotice("已按当前发布版本复制到个人智能体；团队 Release 未发生变化。");
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "复制失败");
    } finally { setBusy(""); }
  }

  async function unshareRelease(agentId: string, version: string) {
    if (!selectedId) return;
    setBusy(`remove:${agentId}:${version}`); setError(""); setNotice("");
    try {
      await request(
        `/api/spaces/${encodeURIComponent(selectedId)}/agents/${encodeURIComponent(agentId)}/releases/${encodeURIComponent(version)}`,
        { method: "DELETE" },
      );
      await loadSpace(selectedId);
      setConfirmingAction("");
      setNotice("Release 授权已撤销；既有任务仍保留其历史快照和记录。");
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "撤销失败");
    } finally { setBusy(""); }
  }

  async function addAcl(agentId: string) {
    if (!selectedId || !aclGrantee) return;
    setBusy(`acl:${agentId}`); setError(""); setNotice("");
    try {
      await request(`/api/spaces/${encodeURIComponent(selectedId)}/agents/${encodeURIComponent(agentId)}/acl`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          grantee_type: "user",
          grantee_id: aclGrantee,
          permission: aclPermission,
        }),
      });
      await loadSpace(selectedId);
      setNotice("Agent 权限已加授。");
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "权限保存失败");
    } finally { setBusy(""); }
  }

  async function removeAcl(agentId: string, granteeId: string, permission: string) {
    if (!selectedId) return;
    setBusy(`acl-remove:${agentId}`); setError(""); setNotice("");
    try {
      await request(
        `/api/spaces/${encodeURIComponent(selectedId)}/agents/${encodeURIComponent(agentId)}/acl/user/${encodeURIComponent(granteeId)}/${permission}`,
        { method: "DELETE" },
      );
      await loadSpace(selectedId);
      setConfirmingAction("");
      setNotice("Agent 权限已撤销。");
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "权限撤销失败");
    } finally { setBusy(""); }
  }

  async function shareKnowledge(event: FormEvent) {
    event.preventDefault();
    if (!selectedId || !knowledgeSelection) return;
    setBusy("knowledge"); setError(""); setNotice("");
    try {
      await request(`/api/spaces/${encodeURIComponent(selectedId)}/knowledge`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ reference: knowledgeSelection }),
      });
      await loadSpace(selectedId);
      setNotice("知识库授权已发布到团队空间；检索仍只发生在成员自己的任务中。");
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "知识库共享失败");
    } finally { setBusy(""); }
  }

  async function unshareKnowledge(reference: string) {
    setBusy(`knowledge:${reference}`); setError(""); setNotice("");
    try {
      await request(
        `/api/spaces/${encodeURIComponent(selectedId)}/knowledge/${encodeURIComponent(reference)}`,
        { method: "DELETE" },
      );
      await loadSpace(selectedId);
      setConfirmingAction("");
      setNotice("知识库空间授权已撤销；历史任务不会被转移或公开。");
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "撤销失败");
    } finally { setBusy(""); }
  }

  return (
    <main className={styles.shell} id="main-content">
      <StudioSidebar active="spaces">
        <div className={styles.railCopy}>
          <strong>个人与团队边界</strong>
          <p>工作区 Agent 拥有稳定身份；Release 切换版本不改变 Agent。</p>
        </div>
      </StudioSidebar>
      <section className={styles.content}>
        <header className={styles.hero}>
          <div><p>Team workspace</p><h1>协作空间</h1><span>集中交付团队可运行的智能体和知识，不共享成员的个人任务记录。</span></div>
          <button
            className={styles.createButton}
            type="button"
            onClick={() => { setCreateError(""); setShowCreate(true); }}
          >
            新建协作空间
          </button>
        </header>

        {showCreate && (
          <div
            className={styles.createBackdrop}
            onMouseDown={(event) => {
              if (event.target === event.currentTarget) closeCreate();
            }}
          >
            <section
              aria-labelledby="create-space-title"
              aria-modal="true"
              className={styles.createDrawer}
              ref={createDialogRef}
              role="dialog"
            >
              <header>
                <div>
                  <p>New team workspace</p>
                  <h2 id="create-space-title">新建协作空间</h2>
                </div>
                <button type="button" onClick={closeCreate} disabled={busy === "create"}>关闭</button>
              </header>
              <form onSubmit={createSpace} className={styles.createForm}>
                <p className={styles.createIntro}>空间用于交付团队可运行的智能体版本和知识授权，不会共享任何成员的个人任务记录。</p>
                <label>
                  <span>空间名称</span>
                  <input
                    ref={createNameRef}
                    value={spaceName}
                    onChange={(event) => setSpaceName(event.target.value)}
                    placeholder="例如：市场研究组"
                    maxLength={160}
                    required
                  />
                  <small>成员会在空间目录和任务入口看到这个名称。</small>
                </label>
                <label>
                  <span>用途说明 <em>可选</em></span>
                  <textarea
                    value={spaceDescription}
                    onChange={(event) => setSpaceDescription(event.target.value)}
                    placeholder="说明这个空间交付哪些智能体和知识。"
                    maxLength={1000}
                    rows={4}
                  />
                </label>
                {createError && <p className={styles.createError} role="alert">{createError}</p>}
                <footer>
                  <span>创建后，你将成为空间所有者。</span>
                  <button type="button" onClick={closeCreate} disabled={busy === "create"}>取消</button>
                  <button disabled={busy === "create"}>{busy === "create" ? "创建中…" : "创建空间"}</button>
                </footer>
              </form>
            </section>
          </div>
        )}

        {(notice || error) && <p className={error ? styles.error : styles.notice} role={error ? "alert" : "status"}>{error || notice}</p>}

        <div className={styles.workspace}>
          <nav className={styles.spaceList} aria-label="团队空间">
            <header><span>我的空间</span><small>{spaces.length}</small></header>
            {loading ? <div className={styles.spaceListLoading}><i /><i /><i /></div> : spaces.length === 0 ? <p>创建空间后，成员和共享资源会显示在这里。</p> : spaces.map((item) => (
              <button key={item.space.spaceId} data-active={item.space.spaceId === selectedId || undefined} onClick={() => { setSelectedId(item.space.spaceId); setActiveView("members"); }}>
                <strong>{item.space.name}</strong><span>{ROLE_LABELS[item.membership.role]}</span><small>{item.space.description || "未填写说明"}</small>
              </button>
            ))}
          </nav>

          {selected ? <div className={styles.detail} aria-busy={spaceLoading}>
            <section className={styles.summary}>
              <div><p>当前空间 · {ROLE_LABELS[selected.membership.role]}</p><h2>{selected.space.name}</h2><span>{selected.space.description || "用于团队共享可运行的智能体版本。"}</span></div>
              <div className={styles.boundaries}><span><b>{spaceLoading ? "…" : members.length}</b> 成员</span><span><b>{spaceLoading ? "…" : workspaceAgents.length}</b> Agent</span><span><b>{spaceLoading ? "…" : sharedKnowledge.length}</b> 知识库</span><span><b>0</b> 共享任务</span></div>
            </section>

            <nav className={styles.resourceTabs} aria-label="空间资源">
              <button type="button" disabled={spaceLoading} aria-current={activeView === "members" ? "page" : undefined} onClick={() => setActiveView("members")}><span>成员</span><small>{spaceLoading ? "…" : members.length}</small></button>
              <button type="button" disabled={spaceLoading} aria-current={activeView === "agents" ? "page" : undefined} onClick={() => setActiveView("agents")}><span>智能体</span><small>{spaceLoading ? "…" : workspaceAgents.length}</small></button>
              <button type="button" disabled={spaceLoading} aria-current={activeView === "knowledge" ? "page" : undefined} onClick={() => setActiveView("knowledge")}><span>知识库</span><small>{spaceLoading ? "…" : sharedKnowledge.length}</small></button>
            </nav>

            {spaceLoading && <section className={styles.resourceLoading} aria-live="polite">
              <div><i /><i /><i /></div>
              <strong>正在切换协作空间</strong>
              <span>同步这个空间的成员、智能体 Release 与知识授权…</span>
            </section>}

            {!spaceLoading && activeView === "members" && <section className={styles.panel}>
              <header><div><p>Members & RBAC</p><h3>空间成员</h3></div><span>Owner › Admin › Contributor › Viewer</span></header>
              {canManage && <form className={styles.inlineForm} onSubmit={addMember}>
                <select value={memberUserId} onChange={(event) => setMemberUserId(event.target.value)}>
                  {directory.map((item) => <option value={item.user.user_id} key={item.user.user_id}>{item.user.display_name} · {item.user.email}</option>)}
                </select>
                <select value={memberRole} onChange={(event) => setMemberRole(event.target.value as SpaceRole)}>
                  <option value="contributor">贡献者</option><option value="viewer">查看者</option><option value="admin">管理员</option><option value="owner">所有者</option>
                </select>
                <button disabled={!memberUserId || busy === "member"}>保存成员</button>
              </form>}
              <div className={styles.memberGrid}>{members.map((member) => {
                const profile = memberById.get(member.userId)?.user;
                const isCurrentUser = member.userId === user.user_id;
                const canRemove = canManage
                  && !isCurrentUser
                  && (selected.membership.role === "owner" || !["owner", "admin"].includes(member.role));
                const confirming = confirmingMemberId === member.userId;
                return <article key={member.userId}>
                  <span>{(profile?.display_name || member.userId).slice(0, 1).toUpperCase()}</span>
                  <div><strong>{profile?.display_name || member.userId}{isCurrentUser ? "（你）" : ""}</strong><small>{profile?.email || "租户成员"}</small></div>
                  <div className={styles.memberActions}>
                    <em>{ROLE_LABELS[member.role]}</em>
                    {canRemove && !confirming && <button type="button" onClick={() => setConfirmingMemberId(member.userId)}>移出</button>}
                    {canRemove && confirming && <>
                      <button type="button" className={styles.danger} disabled={busy === `member-remove:${member.userId}`} onClick={() => void removeMember(member)}>{busy === `member-remove:${member.userId}` ? "移除中" : "确认移除"}</button>
                      <button type="button" disabled={busy === `member-remove:${member.userId}`} onClick={() => setConfirmingMemberId("")}>取消</button>
                    </>}
                  </div>
                </article>;
              })}</div>
            </section>}

            {!spaceLoading && activeView === "agents" && <section className={styles.panel}>
              <header><div><p>Workspace Agents</p><h3>共享智能体</h3></div><span>稳定身份 + 不可变 Release</span></header>
              {canShare && <form className={`${styles.shareForm} ${styles.shareWizard}`} onSubmit={shareAgent}>
                <select value={agentSelection} onChange={(event) => {
                  const nextSelection = event.target.value;
                  const nextAgent = personalAgents.find((agent) => agentKey(agent) === nextSelection);
                  setAgentSelection(nextSelection);
                  setKnowledgeDependencies(nextAgent?.knowledge_references ?? []);
                  setMcpCredentialInputs({});
                }} aria-label="选择个人智能体版本">
                  {personalAgents.map((agent) => <option key={agentKey(agent)} value={agentKey(agent)}>{agent.display_name} · {agent.version}</option>)}
                </select>
                <select value={connectionMode} onChange={(event) => setConnectionMode(event.target.value as "caller_owned" | "service_owned")} aria-label="MCP 凭据模式">
                  <option value="caller_owned">MCP：成员个人凭据</option>
                  <option value="service_owned">MCP：空间共享凭据</option>
                </select>
                <label><input type="checkbox" checked={viewerRunnable} onChange={(event) => setViewerRunnable(event.target.checked)} />允许 Viewer 运行</label>

                <section className={styles.dependencyPanel} aria-label="共享依赖检查">
                  <header><strong>发布依赖</strong><span>Release 共享定义；凭据与知识权限按下列策略处理</span></header>
                  {(selectedAgent?.mcp_references.length ?? 0) === 0 && (selectedAgent?.knowledge_references.length ?? 0) === 0
                    ? <p>该版本没有 MCP 或知识库依赖，可以直接发布。</p>
                    : <>
                      {(selectedAgent?.mcp_references.length ?? 0) > 0 && <div className={styles.dependencyGroup}>
                        <strong>MCP</strong>
                        {selectedAgent?.mcp_references.map((reference) => {
                          const capability = mcpByReference.get(reference);
                          const requiresCredential = capability?.authMode !== "none";
                          const configured = configuredSpaceMcp.has(reference);
                          return <label key={reference} className={!capability?.enabled ? styles.dependencyBlocked : ""}>
                            <span><b>{capability?.label ?? reference}</b><small>{reference}</small></span>
                            {!capability?.enabled
                              ? <em>定义缺失或已停用</em>
                              : connectionMode === "caller_owned"
                                ? <em>{requiresCredential ? "每位成员使用自己的凭据" : "无需凭据"}</em>
                                : configured || !requiresCredential
                                  ? <em>{requiresCredential ? "空间凭据已配置" : "无需凭据"}</em>
                                  : canManage
                                    ? <SecretInput autoComplete="new-password" value={mcpCredentialInputs[reference] ?? ""} onChange={(event) => setMcpCredentialInputs((current) => ({ ...current, [reference]: event.target.value }))} placeholder="输入空间共享凭据" aria-label={`${reference} 空间共享凭据`} revealLabel={`${reference} 空间共享凭据`} />
                                    : <em>需空间管理员配置凭据</em>}
                          </label>;
                        })}
                      </div>}
                      {(selectedAgent?.knowledge_references.length ?? 0) > 0 && <div className={styles.dependencyGroup}>
                        <strong>知识库</strong>
                        {selectedAgent?.knowledge_references.map((reference) => {
                          const alreadyShared = sharedKnowledgeReferences.has(reference);
                          const accessible = accessibleKnowledgeReferences.has(reference);
                          const checked = alreadyShared || knowledgeDependencies.includes(reference);
                          return <label key={reference} className={!alreadyShared && !accessible ? styles.dependencyBlocked : ""}>
                            <input type="checkbox" checked={checked} disabled={alreadyShared || !accessible || !canManage} onChange={(event) => setKnowledgeDependencies((current) => event.target.checked ? [...new Set([...current, reference])] : current.filter((item) => item !== reference))} />
                            <span><b>{knowledgeBases.find((item) => item.reference === reference)?.displayName ?? reference}</b><small>{reference}</small></span>
                            <em>{alreadyShared ? "空间已授权" : !accessible ? "当前账号无权授权" : canManage ? "随 Release 同步授权" : "需空间管理员先授权"}</em>
                          </label>;
                        })}
                      </div>}
                    </>}
                </section>
                {!dependenciesReady && <p className={styles.dependencyWarning}>依赖尚未就绪：补齐 MCP 定义/空间凭据，并确保全部知识库已授权后才能发布。</p>}
                <button disabled={!agentSelection || busy === "share" || !dependenciesReady}>{busy === "share" ? "发布中…" : "确认依赖并发布 Release"}</button>
              </form>}
              <div className={styles.agentList}>{workspaceAgents.length === 0 ? <p>尚未发布共享 Agent。</p> : workspaceAgents.map((item) => {
                const agent = item.agent;
                const releases = releasesByAgent[agent.agentId] ?? [];
                const currentRelease = releases.find(
                  (entry) => entry.release.version === agent.currentVersion,
                );
                const displayName = currentRelease?.agent.display_name
                  || agent.displayName
                  || agent.name;
                const acls = aclsByAgent[agent.agentId] ?? [];
                return <article key={agent.agentId} className={styles.agentCard}>
                  <div><strong>{displayName}</strong><span>{agent.name} · 当前 {agent.currentVersion ?? "未发布"}</span></div>
                  <small>{item.permissions.map((permission) => PERMISSION_LABELS[permission] ?? permission).join(" / ") || "仅查看"}</small>
                  <div className={styles.releaseList}>
                    {releases.map((entry) => (
                      <div key={entry.release.version} className={`${styles.releaseRow}${entry.release.version === agent.currentVersion ? ` ${styles.releaseCurrent}` : ""}`}>
                        <span>{entry.release.version}{entry.release.version === agent.currentVersion ? " · 当前" : ""}</span>
                        <span>{entry.release.connectionMode === "service_owned" ? "共享凭据" : "个人凭据"}</span>
                        <div className={styles.actions}>
                          {item.can_publish && entry.release.version !== agent.currentVersion && <button onClick={() => void promoteRelease(agent.agentId, entry.release.version)} disabled={busy.startsWith("promote:")}>设为当前</button>}
                          {item.can_publish && confirmingAction !== `release:${agent.agentId}:${entry.release.version}` && <button className={styles.danger} onClick={() => setConfirmingAction(`release:${agent.agentId}:${entry.release.version}`)} disabled={busy.startsWith("remove:")} aria-label={`撤销 Release ${entry.release.version}`}>撤销</button>}
                          {item.can_publish && confirmingAction === `release:${agent.agentId}:${entry.release.version}` && <>
                            <button className={styles.danger} onClick={() => void unshareRelease(agent.agentId, entry.release.version)} disabled={busy.startsWith("remove:")}>{busy.startsWith("remove:") ? "撤销中" : "确认撤销"}</button>
                            <button onClick={() => setConfirmingAction("")} disabled={busy.startsWith("remove:")}>取消</button>
                          </>}
                        </div>
                      </div>
                    ))}
                  </div>
                  <div className={styles.actions}>
                    {item.can_chat && agent.currentVersion && <Link className={styles.runLink} href={`/?space=${encodeURIComponent(selectedId)}&agent=${encodeURIComponent(agent.name)}&version=${encodeURIComponent(agent.currentVersion)}`}>开始任务</Link>}
                    {item.can_chat && <button onClick={() => void forkAgent(agent.agentId)} disabled={busy.startsWith("fork:")}>复制到个人</button>}
                  </div>
                  {item.can_manage && (
                    <form className={styles.inlineForm} onSubmit={(event) => { event.preventDefault(); void addAcl(agent.agentId); }}>
                      <select value={aclGrantee} onChange={(event) => setAclGrantee(event.target.value)} aria-label="加授成员">
                        {aclCandidates.map((entry) => <option value={entry.user.user_id} key={entry.user.user_id}>{entry.user.display_name}</option>)}
                      </select>
                      <select value={aclPermission} onChange={(event) => setAclPermission(event.target.value)} aria-label="权限">
                        <option value="chat">对话</option><option value="view">查看</option><option value="edit">编辑</option><option value="publish">发布</option>
                      </select>
                      <button disabled={!aclGrantee || busy.startsWith("acl:")}>加授权限</button>
                    </form>
                  )}
                  {acls.length > 0 && <div className={styles.aclList}>{acls.map((acl) => {
                    const profile = memberById.get(acl.granteeId)?.user;
                    const aclKey = `acl:${agent.agentId}:${acl.granteeId}:${acl.permission}`;
                    return <span key={`${acl.granteeId}:${acl.permission}`}>
                      {profile?.display_name || acl.granteeId} · {PERMISSION_LABELS[acl.permission] ?? acl.permission}
                      {item.can_manage && confirmingAction !== aclKey && <button className={styles.danger} onClick={() => setConfirmingAction(aclKey)} disabled={busy.startsWith("acl-remove:")} aria-label={`撤销 ${profile?.display_name || acl.granteeId} 的${PERMISSION_LABELS[acl.permission] ?? acl.permission}权限`}>撤销</button>}
                      {item.can_manage && confirmingAction === aclKey && <>
                        <button className={styles.danger} onClick={() => void removeAcl(agent.agentId, acl.granteeId, acl.permission)} disabled={busy.startsWith("acl-remove:")}>{busy.startsWith("acl-remove:") ? "撤销中" : "确认"}</button>
                        <button onClick={() => setConfirmingAction("")} disabled={busy.startsWith("acl-remove:")}>取消</button>
                      </>}
                    </span>;
                  })}</div>}
                </article>;
              })}</div>
            </section>}

            {!spaceLoading && activeView === "knowledge" && <section className={styles.panel}>
              <header><div><p>Knowledge grants</p><h3>共享知识库授权</h3></div><span>空间授权可撤销，检索记录仍归运行用户</span></header>
              {canManage && <form className={styles.shareForm} onSubmit={shareKnowledge}>
                <select value={knowledgeSelection} onChange={(event) => setKnowledgeSelection(event.target.value)}>
                  {knowledgeBases.map((base) => <option key={base.reference} value={base.reference}>{base.displayName} · {base.reference}</option>)}
                </select>
                <span />
                <button disabled={!knowledgeSelection || busy === "knowledge"}>授权给空间</button>
              </form>}
              <div className={styles.agentList}>{sharedKnowledge.length === 0 ? <p>尚未共享知识库。</p> : sharedKnowledge.map((item) => {
                const base = knowledgeBases.find((candidate) => candidate.reference === item.knowledgeBaseReference);
                return <article key={item.knowledgeBaseReference}>
                  <div><strong>{base?.displayName ?? item.knowledgeBaseReference}</strong><span>{item.knowledgeBaseReference}</span></div>
                  <small>成员运行共享 Agent 时可检索</small>
                  <div className={styles.actions}>{canManage && confirmingAction !== `knowledge:${item.knowledgeBaseReference}` && <button className={styles.danger} onClick={() => setConfirmingAction(`knowledge:${item.knowledgeBaseReference}`)} disabled={busy.startsWith("knowledge:")}>撤销授权</button>}{canManage && confirmingAction === `knowledge:${item.knowledgeBaseReference}` && <><button className={styles.danger} onClick={() => void unshareKnowledge(item.knowledgeBaseReference)} disabled={busy.startsWith("knowledge:")}>{busy.startsWith("knowledge:") ? "撤销中" : "确认撤销"}</button><button onClick={() => setConfirmingAction("")} disabled={busy.startsWith("knowledge:")}>取消</button></>}</div>
                </article>;
              })}</div>
            </section>}
          </div> : !loading && <div className={styles.empty}><strong>创建第一个协作空间</strong><span>邀请成员，再把个人智能体的不可变版本和知识授权发布到空间。</span><button type="button" onClick={() => { setCreateError(""); setShowCreate(true); }}>开始创建</button></div>}
        </div>
      </section>
    </main>
  );
}
