"use client";

import { useEffect, useMemo, useState } from "react";
import {
  StudioApiError,
  studioClient,
  type StudioCapabilities,
  type StudioCredentialScope,
  type StudioEnvironment,
  type StudioEnvironmentName,
  type StudioEnvironmentResourcePolicy,
} from "../../lib/studio-client";
import styles from "./environment-policy-control-plane.module.css";

type Props = {
  agentName: string;
  environments: StudioEnvironment[];
  capabilities: StudioCapabilities;
  canManage: boolean;
  onUpdated: (environment: StudioEnvironment) => void;
};

const environmentLabels: Record<StudioEnvironmentName, string> = {
  test: "测试",
  canary: "灰度",
  production: "生产",
};

const credentialLabels: Record<StudioCredentialScope, string> = {
  user: "用户",
  team: "团队",
  workload: "工作负载",
};

function copyPolicy(policy: StudioEnvironmentResourcePolicy) {
  return {
    ...policy,
    networkAccess: [...policy.networkAccess],
    allowedModelRoutes: [...policy.allowedModelRoutes],
    allowedMcpReferences: [...policy.allowedMcpReferences],
    allowedKnowledgeReferences: [...policy.allowedKnowledgeReferences],
    credentialScopes: [...policy.credentialScopes],
    quota: { ...policy.quota },
  };
}

function optionalNumber(value: string) {
  if (!value.trim()) return null;
  const parsed = Number(value);
  return Number.isFinite(parsed) && parsed > 0 ? parsed : null;
}

export function EnvironmentPolicyControlPlane({
  agentName,
  environments,
  capabilities,
  canManage,
  onUpdated,
}: Props) {
  const [selectedName, setSelectedName] =
    useState<StudioEnvironmentName>("production");
  const selected = useMemo(
    () => environments.find((item) => item.name === selectedName) ?? environments[0],
    [environments, selectedName],
  );
  const [draft, setDraft] = useState<StudioEnvironmentResourcePolicy | null>(null);
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState("");

  useEffect(() => {
    if (!selected) return;
    setDraft(copyPolicy(selected.resourcePolicy));
    setMessage("");
  }, [selected]);

  if (!selected || !draft) return null;

  const profile = capabilities.executionProfiles.find(
    (item) => item.profileId === draft.executionProfileId,
  );
  const availableProfiles = capabilities.executionProfiles.filter(
    (item) => item.enabled
      && (
        selected.name !== "production"
        || (item.productionAllowed && item.sandboxProvider !== "local")
      ),
  );
  const activeRoutes = capabilities.modelRoutes.filter((item) => item.enabled);
  const activeMcp = capabilities.mcpServers.filter((item) => item.enabled);
  const dirty = JSON.stringify(draft) !== JSON.stringify(selected.resourcePolicy);

  function toggleList(
    field: "allowedModelRoutes" | "allowedMcpReferences",
    value: string,
  ) {
    setDraft((current) => {
      if (!current) return current;
      const values = current[field];
      const next = values.includes(value)
        ? values.filter((item) => item !== value)
        : [...values, value];
      if (field === "allowedModelRoutes" && next.length === 0) return current;
      return { ...current, [field]: next };
    });
  }

  function toggleCredential(scope: StudioCredentialScope) {
    setDraft((current) => {
      if (!current) return current;
      const next = current.credentialScopes.includes(scope)
        ? current.credentialScopes.filter((item) => item !== scope)
        : [...current.credentialScopes, scope];
      return next.length ? { ...current, credentialScopes: next } : current;
    });
  }

  function chooseProfile(profileId: string) {
    const next = capabilities.executionProfiles.find(
      (item) => item.profileId === profileId,
    );
    if (!next) return;
    setDraft((current) => current && ({
      ...current,
      executionProfileId: next.profileId,
      executionProfileVersion: next.version,
      networkProfileId: next.networkPolicyId,
      networkProfileVersion: next.version,
      networkAccess: [...next.networkAccess],
      allowedMcpReferences: current.allowedMcpReferences.filter(
        (reference) => next.allowedMcpReferences.includes(reference),
      ),
    }));
  }

  async function save() {
    if (!canManage || !dirty || !draft) return;
    setSaving(true);
    setMessage("");
    try {
      const updated = await studioClient.replaceEnvironmentPolicy(
        agentName,
        selected,
        draft,
      );
      onUpdated(updated);
      setMessage(`策略 r${updated.policyRevision} 已生效`);
    } catch (reason) {
      setMessage(
        reason instanceof StudioApiError
          ? reason.message
          : reason instanceof Error
            ? reason.message
            : "环境策略保存失败",
      );
    } finally {
      setSaving(false);
    }
  }

  return (
    <section className={styles.root} aria-label="环境资源边界">
      <header className={styles.header}>
        <div>
          <span>ENVIRONMENT BOUNDARY</span>
          <strong>每个新会话固定一份不可变策略快照</strong>
          <small>已存在会话不受后续策略变更影响；不兼容的发布和触发器会在入队前拒绝。</small>
        </div>
        <nav aria-label="选择环境">
          {environments.map((environment) => (
            <button
              type="button"
              key={environment.name}
              data-active={environment.name === selected.name}
              onClick={() => setSelectedName(environment.name)}
            >
              {environmentLabels[environment.name]}
            </button>
          ))}
        </nav>
      </header>

      <div className={styles.snapshot}>
        <div>
          <span>策略</span>
          <strong>r{selected.policyRevision}</strong>
          <code>{selected.policyHash.slice(0, 10)}</code>
        </div>
        <div>
          <span>执行 / 网络</span>
          <strong>{draft.executionProfileId}</strong>
          <code>{draft.networkProfileId} · v{draft.networkProfileVersion}</code>
        </div>
        <div>
          <span>模型与 MCP</span>
          <strong>{draft.allowedModelRoutes.length} / {draft.allowedMcpReferences.length}</strong>
          <code>catalog r{draft.capabilityCatalogRevision}</code>
        </div>
        <div>
          <span>凭据范围</span>
          <strong>{draft.credentialScopes.map((item) => credentialLabels[item]).join(" · ")}</strong>
          <code>运行时租约</code>
        </div>
        <div>
          <span>模型用量</span>
          <strong>费用与 Token 不设执行额度</strong>
          <code>Artifact {(draft.quota.maxArtifactBytes ?? 0).toLocaleString("zh-CN")} B</code>
        </div>
      </div>

      <details className={styles.editor}>
        <summary>
          <span>调整边界</span>
          <small>{dirty ? "有未保存修改" : "当前策略已同步"}</small>
        </summary>
        <div className={styles.editorBody}>
          <label className={styles.profileField}>
            <span>执行 Profile</span>
            <select
              value={draft.executionProfileId}
              disabled={!canManage}
              onChange={(event) => chooseProfile(event.target.value)}
            >
              {availableProfiles.map((item) => (
                <option key={item.profileId} value={item.profileId}>
                  {item.label} · v{item.version}
                </option>
              ))}
            </select>
            <small>{profile?.description ?? "选择平台审核过的执行边界。"}</small>
          </label>

          <fieldset>
            <legend>模型路由</legend>
            {activeRoutes.map((route) => (
              <label key={route.routeId}>
                <input
                  type="checkbox"
                  checked={draft.allowedModelRoutes.includes(route.routeId)}
                  disabled={!canManage}
                  onChange={() => toggleList("allowedModelRoutes", route.routeId)}
                />
                <span>{route.label}</span>
                <code>{route.routeId}</code>
              </label>
            ))}
          </fieldset>

          <fieldset>
            <legend>MCP 资源</legend>
            {activeMcp.map((server) => {
              const profileAllows = profile?.allowedMcpReferences.includes(
                server.reference,
              ) ?? false;
              return (
                <label key={server.reference} data-disabled={!profileAllows}>
                  <input
                    type="checkbox"
                    checked={draft.allowedMcpReferences.includes(server.reference)}
                    disabled={!canManage || !profileAllows}
                    onChange={() => toggleList("allowedMcpReferences", server.reference)}
                  />
                  <span>{server.label}</span>
                  <code>{server.reference}</code>
                </label>
              );
            })}
            {activeMcp.length === 0 && <p>当前目录没有可用 MCP；环境默认拒绝外部工具。</p>}
          </fieldset>

          <fieldset>
            <legend>凭据范围</legend>
            {(["user", "team", "workload"] as StudioCredentialScope[]).map((scope) => (
              <label key={scope}>
                <input
                  type="checkbox"
                  checked={draft.credentialScopes.includes(scope)}
                  disabled={!canManage}
                  onChange={() => toggleCredential(scope)}
                />
                <span>{credentialLabels[scope]}</span>
                <code>{scope}</code>
              </label>
            ))}
          </fieldset>

          <div className={styles.knowledgeBoundary}>
            <div>
              <span>外部知识边界</span>
              <strong>随 MCP 资源统一授权</strong>
              <small>知识库连接与检索工具在上方按已登记的 MCP 引用选择，不在环境策略中手工填写逻辑 ID。</small>
            </div>
            {draft.allowedKnowledgeReferences.length > 0 && (
              <code>
                兼容引用 · {draft.allowedKnowledgeReferences.join(" · ")}
              </code>
            )}
          </div>

          <div className={styles.quotaFields}>
            <label>
              <span>Artifact Bytes</span>
              <input
                type="number"
                min="1"
                step="1024"
                value={draft.quota.maxArtifactBytes ?? ""}
                disabled={!canManage}
                onChange={(event) => setDraft((current) => current && ({
                  ...current,
                  quota: {
                    ...current.quota,
                    maxArtifactBytes: optionalNumber(event.target.value),
                  },
                }))}
              />
            </label>
          </div>

          <footer>
            <div>
              <span data-error={Boolean(message && !message.includes("已生效"))}>
                {message || "保存时自动绑定最新能力目录 revision。"}
              </span>
              <code>environment revision {selected.revision}</code>
            </div>
            <button
              type="button"
              disabled={!canManage || !dirty || saving}
              onClick={() => void save()}
            >
              {saving ? "应用中…" : "应用环境策略"}
            </button>
          </footer>
        </div>
      </details>
    </section>
  );
}
