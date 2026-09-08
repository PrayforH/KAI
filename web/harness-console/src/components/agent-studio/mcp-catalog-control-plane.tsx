"use client";

import {
  type FormEvent,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { useAuth } from "../auth-provider";
import { SecretInput } from "../secret-input";
import { useDialogFocus } from "../../lib/use-dialog-focus";
import { useDismissablePopovers } from "../../lib/use-dismissable-popovers";
import {
  studioClient,
  type StudioCapabilities,
  type StudioCapabilityCatalogRecord,
  type StudioCatalogImpact,
  type StudioDraftSummary,
  type StudioMcpCredentialStatus,
  type StudioMcpDiscoveryResult,
} from "../../lib/studio-client";
import styles from "./mcp-catalog-control-plane.module.css";

type McpCapability = StudioCapabilities["mcpServers"][number];

const MCP_IDENTIFIER_PATTERN =
  /^[a-z][a-z0-9]*(?:[-_][a-z0-9]+)*$/;
const MCP_IDENTIFIER_INPUT_PATTERN =
  "[a-z][a-z0-9]*(?:[-_][a-z0-9]+)*";
const MANAGED_AUTH_HEADER_NAMES = new Set([
  "authorization",
  "cookie",
  "proxy-authorization",
  "set-cookie",
  "x-api-key",
  "api-key",
  "apikey",
]);
const EDITABLE_PLATFORM_MCP_REFERENCES = new Set(["tavily-readonly"]);

const EMPTY_MCP: McpCapability = {
  reference: "",
  ownerUserId: null,
  allowedExecutionProfileIds: [],
  category: "tool",
  serverName: "",
  label: "",
  description: "",
  endpointUrl: "",
  transport: "http",
  customHeaders: {},
  tools: [],
  risk: "medium",
  networkAccess: "external",
  sendsUserData: true,
  readOnly: false,
  credentialManaged: true,
  executionLocation: "external-mcp",
  preflightRequired: true,
  credentialReference: null,
  authMode: "none",
  authName: null,
  authKey: "authorization",
  version: 1,
  enabled: true,
};

const RISK_LABELS = {
  low: "低风险",
  medium: "中风险",
  high: "高风险",
} as const;

const NETWORK_LABELS = {
  none: "无网络",
  internal: "内部网络",
  external: "外部网络",
} as const;

const TRANSPORT_LABELS = {
  http: "Streamable HTTP",
  sse: "SSE",
} as const;

type CatalogSyncImpact = {
  label: string;
  addedTools: string[];
  removedTools: string[];
  agents: Array<Pick<StudioDraftSummary, "draftId" | "displayName" | "publishedVersion">>;
};

function formatDate(value: string) {
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(value));
}

type McpStatusTone = "success" | "disabled" | "warning";

function derivedStatus(
  item: McpCapability,
  credentialConfigured: boolean,
): { key: string; label: string; tone: McpStatusTone } {
  if (!item.enabled) {
    return { key: "disabled", label: "已禁用", tone: "disabled" };
  }
  if (item.authMode !== "none" && !credentialConfigured) {
    return { key: "needs-auth", label: "需要配置凭据", tone: "warning" };
  }
  return { key: "enabled", label: "已启用", tone: "success" };
}

export function McpCatalogControlPlane({
  mode = "mcp",
}: {
  mode?: "mcp" | "knowledge";
}) {
  const knowledgeMode = mode === "knowledge";
  const category = knowledgeMode ? "knowledge" : "tool";
  const { membership } = useAuth();
  const canManage = membership.role !== "viewer";
  useDismissablePopovers();
  const [record, setRecord] = useState<StudioCapabilityCatalogRecord | null>(
    null,
  );
  const [draft, setDraft] = useState<McpCapability>(EMPTY_MCP);
  const [allowedProfileIds, setAllowedProfileIds] = useState<string[]>([]);
  const [discovery, setDiscovery] =
    useState<StudioMcpDiscoveryResult | null>(null);
  const [toolQuery, setToolQuery] = useState("");
  const [searchQuery, setSearchQuery] = useState("");
  const [customHeaderRows, setCustomHeaderRows] = useState<Array<{
    key: string;
    value: string;
  }>>([]);
  const [editingReference, setEditingReference] = useState<string | null>(null);
  const [showForm, setShowForm] = useState(false);
  const [pendingDisable, setPendingDisable] =
    useState<StudioCatalogImpact | null>(null);
  const [pendingDelete, setPendingDelete] = useState<{
    impact: StudioCatalogImpact;
    item: McpCapability;
  } | null>(null);
  const [deleteConfirmation, setDeleteConfirmation] = useState("");
  const [pendingSync, setPendingSync] = useState<CatalogSyncImpact | null>(null);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [credentialValue, setCredentialValue] = useState("");
  const [credentialStatuses, setCredentialStatuses] = useState<
    Record<string, StudioMcpCredentialStatus>
  >({});
  const [detailReference, setDetailReference] = useState<string | null>(null);
  const editorDialogRef = useRef<HTMLElement>(null);
  const syncDialogRef = useRef<HTMLElement>(null);
  const deleteDialogRef = useRef<HTMLElement>(null);
  const detailDrawerRef = useRef<HTMLElement>(null);

  const load = useCallback(async () => {
    try {
      const [next, credentials] = await Promise.all([
        studioClient.catalog(),
        studioClient.listMcpCredentials(),
      ]);
      setRecord(next);
      setCredentialStatuses(
        Object.fromEntries(credentials.map((item) => [item.reference, item])),
      );
      setError("");
    } catch (caught) {
      setError(
        caught instanceof Error ? caught.message : "能力目录暂时不可用。",
      );
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const closeEditor = useCallback(() => {
    if (busy !== "save" && busy !== "discover") setShowForm(false);
  }, [busy]);

  const closeSync = useCallback(() => setPendingSync(null), []);
  const closeDelete = useCallback(() => setPendingDelete(null), []);
  const closeDetail = useCallback(() => setDetailReference(null), []);

  useDialogFocus({
    open: Boolean(showForm && canManage),
    panelRef: editorDialogRef,
    onEscape: closeEditor,
  });
  useDialogFocus({
    open: Boolean(pendingSync),
    panelRef: syncDialogRef,
    onEscape: closeSync,
  });
  useDialogFocus({
    open: Boolean(pendingDelete),
    panelRef: deleteDialogRef,
    onEscape: closeDelete,
  });
  useDialogFocus({
    open: Boolean(detailReference),
    panelRef: detailDrawerRef,
    onEscape: closeDetail,
  });

  const entries = useMemo(
    () =>
      record?.catalog.mcpServers.filter((item) => item.category === category)
      ?? [],
    [category, record],
  );
  const activeCount = useMemo(
    () => entries.filter((item) => item.enabled).length,
    [entries],
  );
  const visibleEntries = useMemo(() => {
    const query = searchQuery.trim().toLowerCase();
    if (!query) return entries;
    return entries.filter((item) => {
      const haystack = [
        item.label,
        item.reference,
        item.description,
        item.endpointUrl ?? "",
      ]
        .join(" ")
        .toLowerCase();
      return haystack.includes(query);
    });
  }, [entries, searchQuery]);

  const groupedEntries = useMemo(() => {
    const groups = [
      {
        key: "personal",
        label: "个人",
        items: visibleEntries.filter((item) => item.ownerUserId),
      },
      {
        key: "builtin",
        label: "内置",
        items: visibleEntries.filter((item) => !item.ownerUserId),
      },
    ];
    return groups.filter((group) => group.items.length > 0);
  }, [visibleEntries]);

  const selectedDetail = useMemo(
    () =>
      detailReference
        ? entries.find((item) => item.reference === detailReference) ?? null
        : null,
    [detailReference, entries],
  );

  function startCreate() {
    const networkAccess = knowledgeMode ? "internal" : "external";
    setDraft({
      ...EMPTY_MCP,
      category,
      networkAccess,
      readOnly: knowledgeMode,
    });
    setAllowedProfileIds(
      record?.catalog.executionProfiles
        .filter(
          (profile) =>
            profile.enabled
            && profile.sandboxProvider === "local"
            && profile.networkAccess.includes(networkAccess),
        )
        .map((profile) => profile.profileId) ?? [],
    );
    setDiscovery(null);
    setToolQuery("");
    setCustomHeaderRows([]);
    setEditingReference(null);
    setPendingDisable(null);
    setPendingDelete(null);
    setPendingSync(null);
    setNotice("");
    setError("");
    setCredentialValue("");
    setShowForm(true);
  }

  function startEdit(item: McpCapability) {
    setDraft({ ...item });
    setAllowedProfileIds([...item.allowedExecutionProfileIds]);
    setDiscovery({
      endpointUrl: item.endpointUrl ?? "",
      transport: item.transport,
      serverName: item.serverName ?? item.reference,
      serverTitle: item.label,
      serverVersion: null,
      latencyMs: 0,
      tools: item.tools.map((canonicalName) => ({
        name: canonicalName.split("__").at(-1) ?? canonicalName,
        canonicalName,
        title: null,
        description: "目录中已审核的工具；重新检测可刷新服务端说明。",
      })),
    });
    setToolQuery("");
    setCustomHeaderRows(
      Object.entries(item.customHeaders).map(([key, value]) => ({ key, value })),
    );
    setEditingReference(item.reference);
    setPendingDisable(null);
    setPendingDelete(null);
    setPendingSync(null);
    setNotice("");
    setError("");
    setCredentialValue("");
    setShowForm(true);
  }

  async function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!record || !canManage) return;
    const reference = draft.reference.trim();
    const serverName = draft.serverName?.trim() || reference;
    const customHeaders = customHeadersFromRows();
    if (!customHeaders) return;
    if (
      !MCP_IDENTIFIER_PATTERN.test(reference)
      || !MCP_IDENTIFIER_PATTERN.test(serverName)
    ) {
      setError(
        "引用标识和服务名须以小写字母开头，可使用小写字母、数字、连字符和单下划线。",
      );
      return;
    }
    if (
      !draft.label.trim() ||
      !draft.description.trim() ||
      !draft.endpointUrl?.trim() ||
      draft.tools.length === 0
    ) {
      setError("名称、说明、MCP 地址和至少一个已检测工具不能为空。");
      return;
    }
    const hasStoredCredential = Boolean(credentialStatuses[reference]?.configured);
    if (
      draft.authMode !== "none"
      && !credentialValue.trim()
      && !hasStoredCredential
    ) {
      setError("该 MCP 需要认证，请填写凭据后再保存。");
      return;
    }
    setBusy("save");
    setError("");
    setNotice("");
    try {
      const previous = record.catalog.mcpServers.find(
        (item) => item.reference === reference,
      );
      let nextCredentialStatus = credentialStatuses[reference];
      if (draft.authMode !== "none" && credentialValue.trim()) {
        nextCredentialStatus = await studioClient.configureMcpCredential(
          reference,
          draft.authKey,
          credentialValue,
        );
      }
      const result = await studioClient.upsertMcp(
        reference,
        record.revision,
        {
          ...draft,
          reference,
          serverName,
          label: draft.label.trim(),
          description: draft.description.trim(),
          endpointUrl: draft.endpointUrl.trim(),
          customHeaders,
          tools: draft.tools,
          credentialReference:
            draft.authMode === "none"
              ? null
              : `STUDIO_MCP_${reference.replace(/[-_]/g, "_").toUpperCase()}`,
          version: editingReference ? draft.version + 1 : draft.version,
          enabled: true,
        },
        allowedProfileIds,
      );
      setRecord(result.record);
      if (draft.authMode === "none") {
        if (nextCredentialStatus?.configured) {
          nextCredentialStatus = await studioClient.deleteMcpCredential(reference);
        }
      }
      if (nextCredentialStatus) {
        setCredentialStatuses((current) => ({
          ...current,
          [reference]: nextCredentialStatus,
        }));
      }
      setCredentialValue("");
      setShowForm(false);
      setEditingReference(null);
      const previousTools = new Set(previous?.tools ?? []);
      const nextTools = new Set(draft.tools);
      const addedTools = draft.tools.filter((tool) => !previousTools.has(tool));
      const removedTools = [...previousTools].filter((tool) => !nextTools.has(tool));
      if (
        previous
        && (addedTools.length > 0 || removedTools.length > 0)
        && result.impact.draftIds.length > 0
      ) {
        let summaries: StudioDraftSummary[] = [];
        try {
          summaries = await studioClient.listDrafts();
        } catch {
          // The catalog update already succeeded. Fall back to stable Draft IDs.
        }
        const byId = new Map(summaries.map((item) => [item.draftId, item]));
        setPendingSync({
          label: draft.label.trim(),
          addedTools,
          removedTools,
          agents: result.impact.draftIds.map((draftId) => {
            const summary = byId.get(draftId);
            return {
              draftId,
              displayName: summary?.displayName ?? draftId,
              publishedVersion: summary?.publishedVersion ?? null,
            };
          }),
        });
      }
      setNotice(
        `${draft.label.trim()} 已${editingReference ? "更新" : "注册"}，已授权 ${allowedProfileIds.length} 个 Execution Profile，目录 revision 为 ${result.record.revision}。`,
      );
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "MCP 未能保存。");
    } finally {
      setBusy("");
    }
  }

  function updateConnection(patch: Partial<McpCapability>) {
    setDraft((current) => ({ ...current, ...patch, tools: [] }));
    if (patch.networkAccess && record) {
      setAllowedProfileIds((current) =>
        current.filter((profileId) => {
          const profile = record.catalog.executionProfiles.find(
            (item) => item.profileId === profileId,
          );
          return Boolean(
            profile?.enabled
            && profile.networkAccess.includes(patch.networkAccess!),
          );
        }),
      );
    }
    setDiscovery(null);
    setToolQuery("");
  }

  function customHeadersFromRows(): Record<string, string> | null {
    const entries = customHeaderRows
      .map((item) => [item.key.trim(), item.value.trim()] as const)
      .filter(([key, value]) => key || value);
    if (entries.some(([key, value]) => !key || !value)) {
      setError("自定义请求头的名称和值必须同时填写。");
      return null;
    }
    const normalized = entries.map(([key]) => key.toLowerCase());
    if (new Set(normalized).size !== normalized.length) {
      setError("自定义请求头名称不能重复。");
      return null;
    }
    if (normalized.some((name) => MANAGED_AUTH_HEADER_NAMES.has(name))) {
      setError("密钥、Token 和 Cookie 不能放入自定义请求头，请使用受管鉴权。");
      return null;
    }
    return Object.fromEntries(entries);
  }

  function toggleAllowedProfile(profileId: string) {
    setAllowedProfileIds((current) =>
      current.includes(profileId)
        ? current.filter((item) => item !== profileId)
        : [...current, profileId],
    );
  }

  async function discover() {
    const reference = draft.reference.trim();
    const serverName = draft.serverName?.trim() || reference;
    const customHeaders = customHeadersFromRows();
    if (!customHeaders) return;
    if (
      !MCP_IDENTIFIER_PATTERN.test(reference) ||
      !MCP_IDENTIFIER_PATTERN.test(serverName) ||
      !draft.endpointUrl?.trim()
    ) {
      setError(
        "先填写有效的引用标识、服务名和 MCP 地址；标识支持小写字母、数字、连字符和单下划线。",
      );
      return;
    }
    if (draft.networkAccess === "none") {
      setError("HTTP MCP 必须选择内部网络或外部网络。");
      return;
    }
    if (
      draft.authMode !== "none"
      && !credentialValue.trim()
      && !credentialStatuses[reference]?.configured
    ) {
      setError("该 MCP 需要认证，请先填写凭据再检测连接。");
      return;
    }
    setBusy("discover");
    setError("");
    setNotice("");
    try {
      const result = await studioClient.discoverMcp({
        reference,
        serverName,
        endpointUrl: draft.endpointUrl.trim(),
        networkAccess: draft.networkAccess,
        customHeaders,
        authMode: draft.authMode,
        authName: draft.authName?.trim() || null,
        authKey: draft.authKey,
        ...(credentialValue.trim()
          ? { credentialValue }
          : {}),
      });
      setDiscovery(result);
      setDraft((current) => ({
        ...current,
        reference,
        serverName,
        endpointUrl: result.endpointUrl,
        transport: result.transport,
        tools: result.tools.map((tool) => tool.canonicalName),
      }));
      setNotice(
        `连接成功：已识别 ${TRANSPORT_LABELS[result.transport]}，${result.serverTitle ?? serverName} 返回 ${result.tools.length} 个工具，耗时 ${result.latencyMs}ms。`,
      );
    } catch (caught) {
      setDiscovery(null);
      setDraft((current) => ({ ...current, tools: [] }));
      setError(
        caught instanceof Error ? caught.message : "MCP 地址检测失败。",
      );
    } finally {
      setBusy("");
    }
  }

  function toggleTool(canonicalName: string) {
    setDraft((current) => ({
      ...current,
      tools: current.tools.includes(canonicalName)
        ? current.tools.filter((item) => item !== canonicalName)
        : [...current.tools, canonicalName],
    }));
  }

  async function inspectDisable(reference: string) {
    setBusy(reference);
    setError("");
    setNotice("");
    try {
      setPendingDisable(await studioClient.catalogImpact("mcp", reference));
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "影响范围读取失败。");
    } finally {
      setBusy("");
    }
  }

  async function disable(reference: string) {
    if (!record || !canManage) return;
    setBusy(reference);
    setError("");
    try {
      const result = await studioClient.disableMcp(reference, record.revision);
      setRecord(result.record);
      setPendingDisable(null);
      setNotice(
        `${reference} 已停用；${result.impact.draftIds.length} 个草稿仍保留引用，发布前会被校验拦截。`,
      );
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "MCP 未能停用。");
    } finally {
      setBusy("");
    }
  }

  async function enable(item: McpCapability) {
    if (!record || !canManage) return;
    setBusy(item.reference);
    setError("");
    try {
      const result = await studioClient.upsertMcp(
        item.reference,
        record.revision,
        { ...item, enabled: true, version: item.version + 1 },
      );
      setRecord(result.record);
      setNotice(`${item.label} 已重新启用。`);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "MCP 未能启用。");
    } finally {
      setBusy("");
    }
  }

  async function inspectDelete(item: McpCapability) {
    setBusy(item.reference);
    setError("");
    setNotice("");
    try {
      const impact = await studioClient.catalogImpact("mcp", item.reference);
      setDeleteConfirmation("");
      setDetailReference(null);
      setPendingDelete({ impact, item });
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "删除影响范围读取失败。");
    } finally {
      setBusy("");
    }
  }

  async function deleteResource() {
    if (!record || !canManage || !pendingDelete) return;
    const { impact, item } = pendingDelete;
    if (impact.draftIds.length > 0 || deleteConfirmation.trim() !== item.reference) {
      return;
    }
    setBusy(item.reference);
    setError("");
    try {
      const result = await studioClient.deleteMcp(item.reference, record.revision);
      setRecord(result.record);
      setCredentialStatuses((current) => {
        const next = { ...current };
        delete next[item.reference];
        return next;
      });
      setPendingDelete(null);
      setDeleteConfirmation("");
      setNotice(`${item.label} 已永久删除。`);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "资源未能删除。");
    } finally {
      setBusy("");
    }
  }

  return (
    <div className={styles.resourceRoot}>
      <section className={styles.content}>
        <header className={styles.hero}>
          <h1>{knowledgeMode ? "接入外部知识库" : "MCP 服务器"}</h1>
        </header>

        <div className={styles.catalogToolbar}>
          <span className={styles.scopeChip}>个人</span>
          <span className={styles.toolbarDivider} aria-hidden="true" />
          <strong>{knowledgeMode ? "知识库" : "MCP"} {record ? entries.length : "—"}</strong>
          <input
            className={styles.searchInput}
            type="search"
            value={searchQuery}
            placeholder={knowledgeMode ? "搜索知识服务…" : "搜索 MCP 服务器…"}
            onChange={(event) => setSearchQuery(event.target.value)}
            aria-label="搜索能力"
          />
        </div>

        {!canManage && (
          <div className={styles.permissionNote}>
            <strong>当前为只读目录</strong>
            <span>Viewer 只能查看；Owner、Admin 和 Member 可管理自己的连接。</span>
          </div>
        )}
        {notice && <p className={styles.notice} role="status">{notice}</p>}
        {error && <p className={styles.error} role="alert">{error}</p>}
        {pendingSync && (
          <div className={styles.dialogBackdrop}>
            <section
              aria-labelledby="catalog-sync-title"
              aria-modal="true"
              className={styles.syncDialog}
              ref={syncDialogRef}
              role="dialog"
            >
              <header>
                <span className={styles.syncMark} aria-hidden="true">↻</span>
                <div>
                  <p>Agent authorization changed</p>
                  <h2 id="catalog-sync-title">这些智能体需要同步</h2>
                </div>
              </header>
              <p>
                「{pendingSync.label}」的工具列表已更新。现有发布版本仍按原授权运行，
                不会自动获得新增工具。
              </p>
              <div className={styles.syncChanges}>
                {pendingSync.addedTools.length > 0 && (
                  <div>
                    <strong>新增 {pendingSync.addedTools.length}</strong>
                    <span>重新预检并发布后才可调用</span>
                    {pendingSync.addedTools.map((tool) => <code key={tool}>{tool}</code>)}
                  </div>
                )}
                {pendingSync.removedTools.length > 0 && (
                  <div data-removed="true">
                    <strong>移除 {pendingSync.removedTools.length}</strong>
                    <span>原发布版本缺少这些工具时会被明确拦截</span>
                    {pendingSync.removedTools.map((tool) => <code key={tool}>{tool}</code>)}
                  </div>
                )}
              </div>
              <div className={styles.affectedAgents}>
                <strong>受影响的智能体</strong>
                <ul>
                  {pendingSync.agents.map((agent) => (
                    <li key={agent.draftId}>
                      <span>{agent.displayName}</span>
                      <small>{agent.publishedVersion ? `已发布 ${agent.publishedVersion}` : "草稿"}</small>
                    </li>
                  ))}
                </ul>
              </div>
              <footer>
                <button type="button" onClick={closeSync}>
                  稍后处理
                </button>
                <a
                  href={`/studio/agents?draft=${encodeURIComponent(
                    pendingSync.agents[0]?.draftId ?? "",
                  )}&section=capabilities&source=knowledge-sync`}
                >
                  去智能体更新
                </a>
              </footer>
            </section>
          </div>
        )}
        {pendingDelete && (
          <div className={styles.dialogBackdrop}>
            <section
              aria-labelledby="catalog-delete-title"
              aria-modal="true"
              className={styles.deleteDialog}
              ref={deleteDialogRef}
              role="dialog"
            >
              <header>
                <div>
                  <p>Permanent deletion</p>
                  <h2 id="catalog-delete-title">
                    删除「{pendingDelete.item.label}」？
                  </h2>
                </div>
                <button
                  aria-label="关闭删除确认"
                  type="button"
                  onClick={closeDelete}
                >
                  ×
                </button>
              </header>
              {pendingDelete.impact.draftIds.length > 0 ? (
                <div className={styles.deleteBlocked}>
                  <strong>暂时不能删除</strong>
                  <span>
                    仍有 {pendingDelete.impact.draftIds.length} 个智能体草稿引用此资源。请先解除绑定：
                    {pendingDelete.impact.draftIds.join("、")}
                  </span>
                </div>
              ) : (
                <label className={styles.deleteConfirmation}>
                  <span>
                    删除后连接定义与托管凭据都会移除，且无法恢复。请输入引用标识
                    <code>{pendingDelete.item.reference}</code>确认。
                  </span>
                  <input
                    value={deleteConfirmation}
                    onChange={(event) => setDeleteConfirmation(event.target.value)}
                  />
                </label>
              )}
              <footer>
                <button type="button" onClick={closeDelete}>
                  取消
                </button>
                <button
                  type="button"
                  disabled={
                    pendingDelete.impact.draftIds.length > 0
                    || deleteConfirmation.trim() !== pendingDelete.item.reference
                    || busy === pendingDelete.item.reference
                  }
                  onClick={() => void deleteResource()}
                >
                  永久删除
                </button>
              </footer>
            </section>
          </div>
        )}

        <section className={styles.catalog} aria-label={knowledgeMode ? "外部知识库连接列表" : "MCP 能力列表"}>
          <header className={styles.catalogHeader}>
            <div>
              <h2>{knowledgeMode ? "已连接" : "已安装"}</h2>
              <span>{record ? activeCount : "—"}</span>
            </div>
            <div className={styles.catalogActions}>
              <button type="button" onClick={() => void load()} aria-label="刷新目录">↻</button>
              {canManage && (
                <button className={styles.primary} type="button" onClick={startCreate}>
                  ＋ {knowledgeMode ? "连接" : "新建"}
                </button>
              )}
            </div>
          </header>
          {!record ? (
            <div className={styles.empty}>正在读取能力目录…</div>
          ) : entries.length === 0 ? (
            <div className={styles.emptyAction}>
              <strong>{knowledgeMode ? "尚未连接外部知识库" : "尚未安装 MCP 服务器"}</strong>
              <span>手动新建服务器，或导入已有配置。</span>
              {canManage && (
                <button type="button" onClick={startCreate}>
                  {knowledgeMode ? "连接知识库" : "新建 MCP 服务器"}
                </button>
              )}
            </div>
          ) : visibleEntries.length === 0 ? (
            <div className={styles.empty}>没有匹配的能力条目</div>
          ) : (
            <div className={styles.groups}>
              {groupedEntries.map((group) => (
                <section className={styles.group} key={group.key}>
                  <header className={styles.groupHeader}>
                    <span>{group.label}</span>
                    <small>{group.items.length}</small>
                  </header>
                  {group.items.map((item) => (
                    <button
                      className={styles.row}
                      type="button"
                      key={item.reference}
                      onClick={() => setDetailReference(item.reference)}
                    >
                      <span className={styles.capabilityGlyph} aria-hidden="true">M</span>
                      <div className={styles.rowCopy}>
                        <strong>{item.label}</strong>
                        <span>{item.description}</span>
                      </div>
                      <code className={styles.rowReference}>{item.reference}</code>
                      <span
                        className={styles.rowStatus}
                        data-tone={derivedStatus(item, credentialStatuses[item.reference]?.configured ?? false).tone}
                      >
                        {derivedStatus(item, credentialStatuses[item.reference]?.configured ?? false).label}
                      </span>
                      <span className={styles.rowArrow} aria-hidden="true">›</span>
                    </button>
                  ))}
                </section>
              ))}
            </div>
          )}
        </section>

        {showForm && canManage && (
          <div className={styles.editorBackdrop}>
          <section
            aria-labelledby="catalog-editor-title"
            aria-modal="true"
            className={styles.editor}
            ref={editorDialogRef}
            role="dialog"
          >
            <header>
              <div>
                <p>Catalog entry</p>
                <h2 id="catalog-editor-title">{editingReference ? (knowledgeMode ? "编辑知识库连接" : "编辑 MCP") : (knowledgeMode ? "连接外部知识库" : "注册 MCP")}</h2>
              </div>
              <button type="button" onClick={closeEditor}>关闭</button>
            </header>
            <form onSubmit={save}>
              <section className={styles.formSection}>
              <div className={styles.formSectionTitle}>
                <span>01</span>
                <div>
                  <strong>基本信息</strong>
                  <small>稳定标识、名称和用途边界</small>
                </div>
              </div>
              <label>
                <span>引用标识</span>
                <input
                  required
                  pattern={MCP_IDENTIFIER_INPUT_PATTERN}
                  disabled={Boolean(editingReference)}
                  placeholder="company-search"
                  value={draft.reference}
                  onChange={(event) =>
                    updateConnection({
                      reference: event.target.value,
                      serverName:
                        draft.serverName === draft.reference || !draft.serverName
                          ? event.target.value
                          : draft.serverName,
                    })
                  }
                />
                <small>
                  智能体通过这个稳定标识绑定能力，创建后不可修改；支持连字符和单下划线。
                </small>
              </label>
              <label>
                <span>MCP 服务名</span>
                <input
                  required
                  pattern={MCP_IDENTIFIER_INPUT_PATTERN}
                  placeholder="company"
                  value={draft.serverName ?? ""}
                  onChange={(event) =>
                    updateConnection({ serverName: event.target.value })
                  }
                />
                <small>
                  用于生成工具名：mcp__服务名__工具名；服务名可保留单下划线。
                </small>
              </label>
              <label>
                <span>显示名称</span>
                <input
                  required
                  placeholder="企业搜索"
                  value={draft.label}
                  onChange={(event) =>
                    setDraft((current) => ({ ...current, label: event.target.value }))
                  }
                />
              </label>
              <label className={styles.wide}>
                <span>能力说明</span>
                <textarea
                  required
                  rows={3}
                  placeholder="说明它能访问什么，以及适合在哪些任务中使用。"
                  value={draft.description}
                  onChange={(event) =>
                    setDraft((current) => ({ ...current, description: event.target.value }))
                  }
                />
              </label>
              </section>
              <section className={styles.formSection}>
              <div className={styles.formSectionTitle}>
                <span>02</span>
                <div>
                  <strong>连接配置</strong>
                  <small>地址、网络范围和非敏感请求头</small>
                </div>
              </div>
              <label className={styles.endpointField}>
                <span>{knowledgeMode ? "知识服务 MCP 地址" : "MCP 地址"}</span>
                <input
                  required
                  type="url"
                  placeholder="https://mcp.example.com/mcp"
                  value={draft.endpointUrl ?? ""}
                  onChange={(event) =>
                    updateConnection({ endpointUrl: event.target.value })
                  }
                />
                <small>不能包含密钥、查询参数或 URL 内嵌账号。</small>
                {discovery && (
                  <small>已自动识别：{TRANSPORT_LABELS[discovery.transport]}</small>
                )}
              </label>
              <div className={styles.transportReadout}>
                <span>传输类型</span>
                <strong>{discovery ? TRANSPORT_LABELS[discovery.transport] : "自动检测"}</strong>
                <small>检测连接时自动识别 SSE 或 Streamable HTTP，避免手工选错。</small>
              </div>
              <section className={styles.customHeaders}>
                <header>
                  <div>
                    <strong>自定义请求头（可选）</strong>
                    <span>用于网关路由和链路标记；密钥、Token、Cookie 必须走下方受管鉴权。</span>
                  </div>
                  <button
                    type="button"
                    disabled={customHeaderRows.length >= 20}
                    onClick={() =>
                      setCustomHeaderRows((current) => [...current, { key: "", value: "" }])
                    }
                  >
                    添加请求头
                  </button>
                </header>
                {customHeaderRows.length > 0 && (
                  <div>
                    {customHeaderRows.map((item, index) => (
                      <div className={styles.customHeaderRow} key={index}>
                        <input
                          aria-label={`请求头 ${index + 1} 名称`}
                          placeholder="X-Tenant-ID"
                          value={item.key}
                          onChange={(event) =>
                            setCustomHeaderRows((current) => current.map((row, rowIndex) =>
                              rowIndex === index ? { ...row, key: event.target.value } : row
                            ))
                          }
                        />
                        <input
                          aria-label={`请求头 ${index + 1} 值`}
                          placeholder="公开路由值（不要填写密钥）"
                          value={item.value}
                          onChange={(event) =>
                            setCustomHeaderRows((current) => current.map((row, rowIndex) =>
                              rowIndex === index ? { ...row, value: event.target.value } : row
                            ))
                          }
                        />
                        <button
                          aria-label={`删除请求头 ${index + 1}`}
                          type="button"
                          onClick={() =>
                            setCustomHeaderRows((current) =>
                              current.filter((_, rowIndex) => rowIndex !== index)
                            )
                          }
                        >
                          ×
                        </button>
                      </div>
                    ))}
                  </div>
                )}
              </section>
              <label>
                <span>风险级别</span>
                <select
                  value={draft.risk}
                  onChange={(event) =>
                    setDraft((current) => ({
                      ...current,
                      risk: event.target.value as McpCapability["risk"],
                    }))
                  }
                >
                  <option value="low">低风险</option>
                  <option value="medium">中风险</option>
                  <option value="high">高风险</option>
                </select>
              </label>
              <label>
                <span>网络范围</span>
                <select
                  value={draft.networkAccess}
                  onChange={(event) =>
                    updateConnection({
                      networkAccess: event.target.value as McpCapability["networkAccess"],
                    })
                  }
                >
                  <option value="internal">内部网络</option>
                  <option value="external">外部网络</option>
                </select>
              </label>
              <label>
                <span>执行位置</span>
                <input
                  required
                  value={draft.executionLocation}
                  onChange={(event) =>
                    setDraft((current) => ({
                      ...current,
                      executionLocation: event.target.value,
                    }))
                  }
                />
              </label>
              </section>
              <section className={styles.formSection}>
              <div className={styles.formSectionTitle}>
                <span>03</span>
                <div>
                  <strong>鉴权</strong>
                  <small>凭据加密托管，保存后不再回显</small>
                </div>
              </div>
              <label>
                <span>鉴权方式</span>
                <select
                  value={draft.authMode}
                  onChange={(event) => {
                    const authMode = event.target.value as McpCapability["authMode"];
                    updateConnection({
                      authMode,
                      authName:
                        authMode === "header"
                          ? "X-API-Key"
                          : authMode === "query"
                            ? "apiKey"
                            : null,
                    });
                  }}
                >
                  <option value="none">无需鉴权</option>
                  <option value="bearer">Bearer Token</option>
                  <option value="header">自定义 Header</option>
                  <option value="query">Query 参数</option>
                </select>
              </label>
              {draft.authMode !== "none" && (
                <label>
                  <span>认证凭据</span>
                  <SecretInput
                    required={!credentialStatuses[draft.reference.trim()]?.configured}
                    autoComplete="new-password"
                    placeholder={
                      credentialStatuses[draft.reference.trim()]?.configured
                        ? "已配置；留空则不更新"
                        : "填写 Token 或 API Key"
                    }
                    value={credentialValue}
                    onChange={(event) => setCredentialValue(event.target.value)}
                    revealLabel="认证凭据"
                  />
                  <small>
                    {credentialStatuses[draft.reference.trim()]?.configured
                      ? "凭据已加密保存；为安全起见不会回显原值。"
                      : "保存前仅用于连接检测，保存后加密托管。"}
                  </small>
                </label>
              )}
              {(draft.authMode === "header" || draft.authMode === "query") && (
                <label>
                  <span>{draft.authMode === "header" ? "Header 名称" : "参数名称"}</span>
                  <input
                    required
                    placeholder={draft.authMode === "header" ? "X-API-Key" : "apiKey"}
                    value={draft.authName ?? ""}
                    onChange={(event) =>
                      updateConnection({ authName: event.target.value })
                    }
                  />
                </label>
              )}
              {draft.authMode !== "none" && (
                <label>
                  <span>凭据映射键</span>
                  <input
                    required
                    pattern="[a-z][a-z0-9_]*"
                    value={draft.authKey}
                    onChange={(event) =>
                      updateConnection({ authKey: event.target.value })
                    }
                  />
                  <small>对应服务端引用 JSON 中的键。</small>
                </label>
              )}
              </section>
              <section className={styles.formSection}>
              <div className={styles.formSectionTitle}>
                <span>04</span>
                <div>
                  <strong>运行边界</strong>
                  <small>Execution Profile、数据发送和预检要求</small>
                </div>
              </div>
              <section className={styles.profileAuthorization}>
                <header>
                  <div>
                    <strong>允许在哪些 Execution Profile 中使用</strong>
                    <span>
                      与 MCP 定义原子保存；未勾选的 Profile 会继续拒绝该连接。
                    </span>
                  </div>
                  <span>{allowedProfileIds.length} 个已授权</span>
                </header>
                <div>
                  {(record?.catalog.executionProfiles ?? []).map((profile) => {
                    const networkCompatible = profile.networkAccess.includes(
                      draft.networkAccess,
                    );
                    const needsPrivateRouteConfirmation =
                      draft.networkAccess === "internal"
                      && profile.sandboxProvider !== "local";
                    return (
                      <label
                        data-compatible={networkCompatible && profile.enabled}
                        key={profile.profileId}
                      >
                        <input
                          type="checkbox"
                          checked={allowedProfileIds.includes(profile.profileId)}
                          disabled={!networkCompatible || !profile.enabled}
                          onChange={() => toggleAllowedProfile(profile.profileId)}
                        />
                        <span>
                          <strong>{profile.label}</strong>
                          <code>{profile.profileId}</code>
                          <small>
                            {!profile.enabled
                              ? "Profile 已停用"
                              : !networkCompatible
                                ? `不支持${NETWORK_LABELS[draft.networkAccess]}`
                                : profile.sandboxProvider === "local"
                                  ? "本地 Preview；内网连接的安全默认项"
                                  : needsPrivateRouteConfirmation
                                    ? "生产授权前，请确认该 Sandbox 能访问此内网地址"
                                    : "显式授权后可在此隔离环境调用"}
                          </small>
                        </span>
                      </label>
                    );
                  })}
                </div>
              </section>
              </section>
              <section className={styles.formSection}>
              <div className={styles.formSectionTitle}>
                <span>05</span>
                <div>
                  <strong>连接测试与工具</strong>
                  <small>真实执行 initialize 与 tools/list 后再保存</small>
                </div>
              </div>
              <div className={styles.discoveryAction}>
                <div>
                  <strong>检测连接并识别工具</strong>
                  <span>服务端执行 initialize 和 tools/list，不会调用任何业务工具。</span>
                </div>
                <button
                  type="button"
                  disabled={busy === "discover"}
                  onClick={() => void discover()}
                >
                  {busy === "discover" ? "正在检测…" : discovery ? "重新检测" : "检测地址"}
                </button>
              </div>
              {discovery && (
                <section className={styles.toolPicker}>
                  <header>
                    <div>
                      <strong>{discovery.tools.length} 个工具可用</strong>
                      <span>已选择 {draft.tools.length} 个，保存后只有所选工具可被智能体调用。</span>
                    </div>
                    <input
                      type="search"
                      placeholder="搜索工具"
                      value={toolQuery}
                      onChange={(event) => setToolQuery(event.target.value)}
                    />
                    <button
                      type="button"
                      onClick={() =>
                        setDraft((current) => ({
                          ...current,
                          tools: discovery.tools.map((tool) => tool.canonicalName),
                        }))
                      }
                    >
                      全选
                    </button>
                    <button
                      type="button"
                      onClick={() =>
                        setDraft((current) => ({ ...current, tools: [] }))
                      }
                    >
                      清空
                    </button>
                  </header>
                  <div>
                    {discovery.tools
                      .filter((tool) =>
                        `${tool.name} ${tool.title ?? ""} ${tool.description}`
                          .toLowerCase()
                          .includes(toolQuery.trim().toLowerCase()),
                      )
                      .map((tool) => (
                        <label key={tool.canonicalName}>
                          <input
                            type="checkbox"
                            checked={draft.tools.includes(tool.canonicalName)}
                            onChange={() => toggleTool(tool.canonicalName)}
                          />
                          <span>
                            <strong>{tool.title ?? tool.name}</strong>
                            <code>{tool.name}</code>
                            <small>{tool.description || "服务端未提供工具说明"}</small>
                          </span>
                        </label>
                      ))}
                  </div>
                </section>
              )}
              <div className={styles.checks}>
                <label>
                  <input
                    type="checkbox"
                    checked={draft.readOnly}
                    onChange={(event) =>
                      setDraft((current) => ({
                        ...current,
                        readOnly: event.target.checked,
                      }))
                    }
                  />
                  <span>只读能力（允许 Worker 懒加载直连）</span>
                </label>
                <label>
                  <input
                    type="checkbox"
                    checked={draft.sendsUserData}
                    onChange={(event) =>
                      setDraft((current) => ({
                        ...current,
                        sendsUserData: event.target.checked,
                      }))
                    }
                  />
                  <span>调用会向外部服务发送用户数据</span>
                </label>
                <label>
                  <input
                    type="checkbox"
                    checked={draft.preflightRequired}
                    onChange={(event) =>
                      setDraft((current) => ({
                        ...current,
                        preflightRequired: event.target.checked,
                      }))
                    }
                  />
                  <span>运行前必须通过预检</span>
                </label>
              </div>
              </section>
              <div className={styles.formActions}>
                <span>已审核 {draft.tools.length} 个工具；保存后可在「智能体 → 工具与 MCP」中绑定。</span>
                <button type="button" onClick={() => setShowForm(false)}>取消</button>
                <button type="submit" disabled={busy === "save"}>
                  {busy === "save" ? "正在保存…" : editingReference ? "保存更新" : "完成注册"}
                </button>
              </div>
            </form>
          </section>
          </div>
        )}
      </section>

      {selectedDetail && (
        <div
          className={styles.detailBackdrop}
          onMouseDown={(event) => {
            if (event.target === event.currentTarget) closeDetail();
          }}
        >
          <aside
            className={styles.detailDrawer}
            ref={detailDrawerRef}
            role="dialog"
            aria-modal="true"
            aria-label={`${selectedDetail.label} 详情`}
            tabIndex={-1}
          >
            <header className={styles.detailHeader}>
              <div>
                <span className={styles.capabilityMark} aria-hidden="true">
                  {knowledgeMode ? "KB" : "MCP"}
                </span>
                <div>
                  <strong>{selectedDetail.label}</strong>
                  <code>{selectedDetail.reference}</code>
                </div>
              </div>
              <button type="button" onClick={closeDetail} aria-label="关闭详情">
                ×
              </button>
            </header>
            <div className={styles.detailBody}>
            <p>{selectedDetail.description}</p>
            <dl className={styles.detailFacts}>
              <div>
                <dt>状态</dt>
                <dd>
                  <span
                    className={styles.status}
                    data-tone={derivedStatus(
                      selectedDetail,
                      credentialStatuses[selectedDetail.reference]?.configured ?? false,
                    ).tone}
                  >
                    <i className={styles.statusDot} aria-hidden="true" />
                    {derivedStatus(
                      selectedDetail,
                      credentialStatuses[selectedDetail.reference]?.configured ?? false,
                    ).label}
                  </span>
                </dd>
              </div>
              <div><dt>传输</dt><dd>{TRANSPORT_LABELS[selectedDetail.transport]}</dd></div>
              <div><dt>网络</dt><dd>{NETWORK_LABELS[selectedDetail.networkAccess]}</dd></div>
              <div><dt>风险</dt><dd>{RISK_LABELS[selectedDetail.risk]}</dd></div>
              <div><dt>版本</dt><dd>v{selectedDetail.version}</dd></div>
              <div><dt>来源</dt><dd>{selectedDetail.ownerUserId ? "个人" : "平台内置"}</dd></div>
              <div>
                <dt>认证</dt>
                <dd>
                  {selectedDetail.authMode === "none"
                    ? "无需认证"
                    : credentialStatuses[selectedDetail.reference]?.configured
                      ? "凭据已配置"
                      : "等待配置凭据"}
                </dd>
              </div>
              <div>
                <dt>Profile 授权</dt>
                <dd>
                  {(record?.catalog.executionProfiles ?? []).filter(
                    (profile) =>
                      profile.enabled
                      && profile.allowedMcpReferences.includes(selectedDetail.reference),
                  ).length > 0
                    ? `${(record?.catalog.executionProfiles ?? []).filter(
                        (profile) =>
                          profile.enabled
                          && profile.allowedMcpReferences.includes(selectedDetail.reference),
                      ).length} 个 Profile 已授权`
                    : "尚未授权 Profile"}
                </dd>
              </div>
            </dl>
            {selectedDetail.endpointUrl && (
              <section className={styles.detailSection}>
                <h4>MCP 地址</h4>
                <code>{selectedDetail.endpointUrl}</code>
              </section>
            )}
            <section className={styles.detailSection}>
              <h4>引用工具（{selectedDetail.tools.length}）</h4>
              <div className={styles.tools}>
                {selectedDetail.tools.map((tool) => <code key={tool}>{tool}</code>)}
              </div>
            </section>
            {Object.keys(selectedDetail.customHeaders).length > 0 && (
              <section className={styles.detailSection}>
                <h4>自定义 Header</h4>
                <ul className={styles.detailHeaders}>
                  {Object.entries(selectedDetail.customHeaders).map(([key, value]) => (
                    <li key={key}>
                      <code>{key}</code>
                      <span>{value}</span>
                    </li>
                  ))}
                </ul>
              </section>
            )}
            <section className={styles.detailSection}>
              <h4>最近状态</h4>
              <span>
                {credentialStatuses[selectedDetail.reference]?.updatedAt
                  ? `凭据更新于 ${formatDate(credentialStatuses[selectedDetail.reference]!.updatedAt!)} · ${credentialStatuses[selectedDetail.reference]!.updatedBy ?? "—"}`
                  : "暂无凭据更新记录"}
              </span>
            </section>
            {pendingDisable?.resourceId === selectedDetail.reference && (
              <div className={styles.impact}>
                <div>
                  <strong>确认停用？</strong>
                  <span>
                    {pendingDisable.draftIds.length === 0
                      ? "没有草稿引用此能力。"
                      : `${pendingDisable.draftIds.length} 个草稿仍在引用：${pendingDisable.draftIds.join("、")}`}
                  </span>
                </div>
                <button type="button" onClick={() => setPendingDisable(null)}>
                  取消
                </button>
                <button
                  type="button"
                  disabled={busy === selectedDetail.reference}
                  onClick={() => void disable(selectedDetail.reference)}
                >
                  确认停用
                </button>
              </div>
            )}
            </div>
            {canManage
              && (selectedDetail.ownerUserId
                || EDITABLE_PLATFORM_MCP_REFERENCES.has(selectedDetail.reference)) && (
              <footer className={styles.drawerActions}>
              <button type="button" onClick={() => startEdit(selectedDetail)}>
                编辑
              </button>
              {selectedDetail.enabled ? (
                <button
                  type="button"
                  disabled={busy === selectedDetail.reference}
                  onClick={() => void inspectDisable(selectedDetail.reference)}
                >
                  停用
                </button>
              ) : (
                <button
                  type="button"
                  disabled={busy === selectedDetail.reference}
                  onClick={() => void enable(selectedDetail)}
                >
                  重新启用
                </button>
              )}
              <details className={styles.actionMenu} data-dismiss-on-outside>
                <summary aria-label={`${selectedDetail.label} 更多操作`}>更多</summary>
                <div>
                  <button
                    className={styles.deleteAction}
                    type="button"
                    disabled={busy === selectedDetail.reference}
                    onClick={() => void inspectDelete(selectedDetail)}
                  >
                    删除
                  </button>
                </div>
              </details>
              </footer>
            )}
          </aside>
        </div>
      )}
    </div>
  );
}
