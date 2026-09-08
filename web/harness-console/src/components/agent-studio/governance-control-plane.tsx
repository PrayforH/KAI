"use client";

import { useEffect, useMemo, useState } from "react";
import {
  StudioApiError,
  studioClient,
  type StudioCallPolicyRule,
  type StudioConnectionScope,
  type StudioContextTrust,
  type StudioCredentialConnection,
  type StudioGovernedPolicy,
  type StudioPolicyImpact,
  type StudioPolicySimulation,
  type StudioResultPolicyRule,
} from "../../lib/studio-client";
import styles from "./governance-control-plane.module.css";
import { PRODUCT_NAME } from "../product-brand";

type Props = {
  agentName: string;
  policyId: string;
  mcpReferences: string[];
  mcpTools: string[];
  canManage: boolean;
  policies: StudioGovernedPolicy[];
  onPoliciesChanged: (policies: StudioGovernedPolicy[]) => void;
};

const scopeLabels: Record<StudioConnectionScope, string> = {
  personal: "个人",
  team: "团队",
  workload: "工作负载",
};

const trustLabels: Record<StudioContextTrust, string> = {
  safe: "安全",
  sensitive: "敏感",
  untrusted: "不可信",
};

function defaultCallRules(
  policyId: string,
  mcpTools: readonly string[],
): StudioCallPolicyRule[] {
  const rules: StudioCallPolicyRule[] = [
    { name: "allow-read", decision: "allow", tool: "Read", priority: 0 },
    { name: "allow-glob", decision: "allow", tool: "Glob", priority: 0 },
    { name: "allow-grep", decision: "allow", tool: "Grep", priority: 0 },
    {
      name: "allow-tool-directory",
      decision: "allow",
      tool: "ToolSearch",
      priority: 0,
    },
    {
      name: "allow-mcp-directory",
      decision: "allow",
      tool: "MCPSearch",
      priority: 0,
    },
    ...mcpTools.map((tool, index) => ({
      name: `allow-selected-mcp-${index + 1}`,
      decision: "allow" as const,
      tool,
      priority: 0,
    })),
  ];
  if (policyId !== "production-read-only") {
    rules.push(
      { name: "allow-write", decision: "allow", tool: "Write", priority: 0 },
      { name: "allow-edit", decision: "allow", tool: "Edit", priority: 0 },
      { name: "review-bash", decision: "ask", tool: "Bash", priority: 0 },
    );
  }
  if (policyId.includes("orchestrator")) {
    rules.push({
      name: "allow-delegation",
      decision: "allow",
      tool: "Task",
      priority: 0,
    });
  }
  return rules;
}

function messageFrom(reason: unknown, fallback: string) {
  if (reason instanceof StudioApiError || reason instanceof Error) {
    return reason.message;
  }
  return fallback;
}

export function GovernanceControlPlane({
  agentName,
  policyId,
  mcpReferences,
  mcpTools,
  canManage,
  policies,
  onPoliciesChanged,
}: Props) {
  const [connections, setConnections] = useState<StudioCredentialConnection[]>([]);
  const [loading, setLoading] = useState(true);
  const [action, setAction] = useState("");
  const [message, setMessage] = useState("");
  const [callRules, setCallRules] = useState<StudioCallPolicyRule[]>([]);
  const [resultRules, setResultRules] = useState<StudioResultPolicyRule[]>([]);
  const [simulationTool, setSimulationTool] = useState("Write");
  const [simulationTrust, setSimulationTrust] =
    useState<StudioContextTrust>("safe");
  const [simulation, setSimulation] = useState<StudioPolicySimulation | null>(null);
  const [impact, setImpact] = useState<StudioPolicyImpact | null>(null);
  const [connectionDraft, setConnectionDraft] = useState({
    connectionId: "",
    displayName: "",
    resourceReference: mcpReferences[0] ?? "",
    scope: "personal" as StudioConnectionScope,
    principalId: "",
    secretReference: mcpReferences[0]
      ? `settings://mcp/${mcpReferences[0]}`
      : "",
    requiredKeys: "api_key",
  });
  const policy = useMemo(
    () => policies.find((item) => item.policyId === policyId) ?? null,
    [policies, policyId],
  );

  useEffect(() => {
    let active = true;
    async function load() {
      setLoading(true);
      try {
        const values = await studioClient.listConnections();
        if (active) setConnections(values);
      } catch (reason) {
        if (active) setMessage(messageFrom(reason, "连接列表读取失败"));
      } finally {
        if (active) setLoading(false);
      }
    }
    void load();
    return () => { active = false; };
  }, []);

  useEffect(() => {
    setCallRules(policy?.callRules.map((rule) => ({ ...rule })) ?? []);
    setResultRules(policy?.resultRules.map((rule) => ({ ...rule })) ?? []);
    setSimulation(null);
    setImpact(null);
  }, [policy]);

  useEffect(() => {
    const fallbackReference = mcpReferences[0] ?? "";
    setConnectionDraft((current) => {
      if (
        current.connectionId.trim()
        || mcpReferences.includes(current.resourceReference)
      ) {
        return current;
      }
      return {
        ...current,
        resourceReference: fallbackReference,
        secretReference: fallbackReference
          ? `settings://mcp/${fallbackReference}`
          : "",
      };
    });
  }, [mcpReferences]);

  const policyDirty = Boolean(
    policy
    && (
      JSON.stringify(callRules) !== JSON.stringify(policy.callRules)
      || JSON.stringify(resultRules) !== JSON.stringify(policy.resultRules)
    ),
  );
  const explicitlyAllowedTools = new Set(
    callRules
      .filter((rule) => rule.decision === "allow" && rule.tool)
      .map((rule) => rule.tool),
  );
  const missingMcpTools = mcpTools.filter(
    (tool) => !explicitlyAllowedTools.has(tool),
  );
  const activeConnections = connections.filter((item) => item.status === "active");

  async function reloadPolicies() {
    const values = await studioClient.listGovernedPolicies();
    onPoliciesChanged(values);
    return values;
  }

  async function createConnection() {
    if (!canManage || !connectionDraft.connectionId.trim()) return;
    setAction("create-connection");
    setMessage("");
    try {
      const created = await studioClient.createConnection({
        connectionId: connectionDraft.connectionId.trim(),
        displayName:
          connectionDraft.displayName.trim()
          || connectionDraft.connectionId.trim(),
        resourceKind: "mcp",
        resourceReference: connectionDraft.resourceReference.trim(),
        scope: connectionDraft.scope,
        principalId: connectionDraft.principalId.trim(),
        secretReference: connectionDraft.secretReference.trim(),
        requiredKeys: connectionDraft.requiredKeys
          .split(",")
          .map((item) => item.trim())
          .filter(Boolean),
      });
      setConnections((current) => [...current, created]);
      setConnectionDraft((current) => ({
        ...current,
        connectionId: "",
        displayName: "",
        principalId: "",
      }));
      setMessage("连接已登记；秘密值仍由服务端 Secret Provider 管理");
    } catch (reason) {
      setMessage(messageFrom(reason, "连接创建失败"));
    } finally {
      setAction("");
    }
  }

  async function revokeConnection(connection: StudioCredentialConnection) {
    if (!canManage || connection.status !== "active") return;
    setAction(`revoke:${connection.connectionId}`);
    setMessage("");
    try {
      const revoked = await studioClient.revokeConnection(connection);
      setConnections((current) => current.map((item) =>
        item.connectionId === revoked.connectionId ? revoked : item
      ));
      setMessage(`${connection.displayName} 已撤销；关联 lease 将立即失效`);
    } catch (reason) {
      setMessage(messageFrom(reason, "连接撤销失败"));
    } finally {
      setAction("");
    }
  }

  async function initializePolicy() {
    if (!canManage || policy) return;
    setAction("initialize-policy");
    setMessage("");
    try {
      await studioClient.createGovernedPolicy({
        policyId,
        displayName: `${policyId} · 租户策略`,
        description: `由${PRODUCT_NAME}发布的确定性调用与结果策略。`,
        callRules: defaultCallRules(policyId, mcpTools),
        resultRules: [],
      });
      await reloadPolicies();
      setMessage("策略草稿已创建；发布前先运行模拟和影响预览");
    } catch (reason) {
      setMessage(messageFrom(reason, "策略草稿创建失败"));
    } finally {
      setAction("");
    }
  }

  async function savePolicy() {
    if (!canManage || !policy || !policyDirty) return;
    setAction("save-policy");
    setMessage("");
    try {
      await studioClient.replaceGovernedPolicy(policy, {
        displayName: policy.displayName,
        description: policy.description,
        callRules,
        resultRules,
      });
      await reloadPolicies();
      setMessage("策略草稿已保存，运行仍使用上一个已发布版本");
    } catch (reason) {
      setMessage(messageFrom(reason, "策略保存失败"));
    } finally {
      setAction("");
    }
  }

  function scenario() {
    return {
      scenarioId: `studio-${simulationTool.toLocaleLowerCase().replaceAll("_", "-")}`,
      agentName,
      toolName: simulationTool.trim(),
      arguments:
        simulationTool === "Bash"
          ? { command: "git status" }
          : simulationTool === "Write" || simulationTool === "Edit"
            ? { file_path: "output/report.md" }
            : {},
      sandboxIsolation: "workspace" as const,
      contextTrust: simulationTrust,
    };
  }

  async function simulate() {
    if (!policy || !simulationTool.trim()) return;
    setAction("simulate");
    setMessage("");
    try {
      setSimulation(
        await studioClient.simulateGovernedPolicy(policy.policyId, scenario()),
      );
    } catch (reason) {
      setMessage(messageFrom(reason, "策略模拟失败"));
    } finally {
      setAction("");
    }
  }

  async function previewImpact() {
    if (!policy || policyDirty || !simulationTool.trim()) return;
    setAction("impact");
    setMessage("");
    try {
      setImpact(
        await studioClient.previewGovernedPolicyImpact(
          policy.policyId,
          [scenario()],
        ),
      );
    } catch (reason) {
      setMessage(messageFrom(reason, "影响预览失败"));
    } finally {
      setAction("");
    }
  }

  async function publishPolicy() {
    if (!canManage || !policy || policyDirty) return;
    setAction("publish-policy");
    setMessage("");
    try {
      const publication = await studioClient.publishGovernedPolicy(policy);
      await reloadPolicies();
      setMessage(
        `已发布 r${publication.revision} · ${publication.contentHash.slice(0, 10)}`,
      );
    } catch (reason) {
      setMessage(messageFrom(reason, "策略发布失败"));
    } finally {
      setAction("");
    }
  }

  function updateCallRule(index: number, update: Partial<StudioCallPolicyRule>) {
    setCallRules((current) => current.map((rule, currentIndex) =>
      currentIndex === index ? { ...rule, ...update } : rule
    ));
  }

  function updateResultRule(index: number, update: Partial<StudioResultPolicyRule>) {
    setResultRules((current) => current.map((rule, currentIndex) =>
      currentIndex === index ? { ...rule, ...update } : rule
    ));
  }

  function syncMcpTools() {
    if (!canManage || missingMcpTools.length === 0) return;
    setCallRules((current) => [
      ...current,
      ...missingMcpTools.map((tool, index) => ({
        name: `allow-agent-mcp-${current.length + index + 1}`,
        decision: "allow" as const,
        tool,
        priority: 0,
      })),
    ]);
    setMessage(
      `已把 ${missingMcpTools.length} 个 MCP 工具加入策略草稿；保存并发布后才会影响真实 Run`,
    );
  }

  return (
    <section className={styles.root} aria-label="连接与策略治理">
      <header className={styles.header}>
        <div>
          <span>CONNECTIONS & POLICY</span>
          <strong>运行时身份、短期凭证与确定性策略</strong>
          <small>数据库只保存连接引用和不可变策略快照；秘密值不会进入浏览器、Manifest 或事件。</small>
        </div>
        <code>{policyId}</code>
      </header>

      <div className={styles.summary}>
        <div>
          <span>有效连接</span>
          <strong>{activeConnections.length}</strong>
          <small>{connections.length - activeConnections.length} 已撤销</small>
        </div>
        <div>
          <span>策略草稿</span>
          <strong>{policy ? `r${policy.revision}` : "内置"}</strong>
          <small>{policy?.publishedRevision ? `已发布 r${policy.publishedRevision}` : "尚无租户发布"}</small>
        </div>
        <div>
          <span>规则</span>
          <strong>{policy ? `${policy.callRules.length} / ${policy.resultRules.length}` : "—"}</strong>
          <small>调用 / 结果</small>
        </div>
      </div>

      <details className={styles.section}>
        <summary>
          <span>凭证连接</span>
          <small>{loading ? "读取中…" : `${connections.length} 条连接记录`}</small>
        </summary>
        <div className={styles.sectionBody}>
          <div className={styles.connectionList}>
            {connections.length ? connections.map((connection) => (
              <article
                key={connection.connectionId}
                data-status={connection.status}
              >
                <span className={styles.statusDot} aria-hidden="true" />
                <div>
                  <strong>{connection.displayName}</strong>
                  <small>
                    {connection.resourceKind}:{connection.resourceReference}
                    {" · "}{scopeLabels[connection.scope]}:{connection.principalId}
                  </small>
                </div>
                <code>{connection.secretReference}</code>
                <span>{connection.requiredKeys.join(" · ") || "无字段"}</span>
                <button
                  type="button"
                  disabled={
                    !canManage
                    || connection.status !== "active"
                    || Boolean(action)
                  }
                  onClick={() => void revokeConnection(connection)}
                >
                  {action === `revoke:${connection.connectionId}`
                    ? "撤销中…"
                    : connection.status === "revoked"
                      ? "已撤销"
                      : "撤销"}
                </button>
              </article>
            )) : (
              <p className={styles.empty}>尚未登记连接。未托管资源继续使用平台审核过的服务端默认引用。</p>
            )}
          </div>

          <div className={styles.connectionEditor}>
            <label>
              <span>连接 ID</span>
              <input
                value={connectionDraft.connectionId}
                placeholder="personal-tavily"
                disabled={!canManage}
                onChange={(event) => setConnectionDraft((current) => ({
                  ...current,
                  connectionId: event.target.value,
                }))}
              />
            </label>
            <label>
              <span>显示名称</span>
              <input
                value={connectionDraft.displayName}
                placeholder="个人搜索连接"
                disabled={!canManage}
                onChange={(event) => setConnectionDraft((current) => ({
                  ...current,
                  displayName: event.target.value,
                }))}
              />
            </label>
            <label>
              <span>MCP 资源</span>
              <select
                value={connectionDraft.resourceReference}
                disabled={!canManage}
                onChange={(event) => setConnectionDraft((current) => ({
                  ...current,
                  resourceReference: event.target.value,
                  secretReference: `settings://mcp/${event.target.value}`,
                }))}
              >
                {mcpReferences.map((reference) => (
                  <option key={reference} value={reference}>{reference}</option>
                ))}
                {!mcpReferences.length && <option value="">当前 Agent 未绑定 MCP</option>}
              </select>
            </label>
            <label>
              <span>作用域</span>
              <select
                value={connectionDraft.scope}
                disabled={!canManage}
                onChange={(event) => setConnectionDraft((current) => ({
                  ...current,
                  scope: event.target.value as StudioConnectionScope,
                }))}
              >
                {Object.entries(scopeLabels).map(([value, label]) => (
                  <option key={value} value={value}>{label}</option>
                ))}
              </select>
            </label>
            <label>
              <span>主体 ID</span>
              <input
                value={connectionDraft.principalId}
                placeholder={connectionDraft.scope === "workload" ? "trigger:nightly" : "user / team id"}
                disabled={!canManage}
                onChange={(event) => setConnectionDraft((current) => ({
                  ...current,
                  principalId: event.target.value,
                }))}
              />
            </label>
            <label>
              <span>Secret 引用</span>
              <input
                value={connectionDraft.secretReference}
                placeholder="settings://mcp/tavily"
                disabled={!canManage}
                onChange={(event) => setConnectionDraft((current) => ({
                  ...current,
                  secretReference: event.target.value,
                }))}
              />
            </label>
            <label>
              <span>必需字段</span>
              <input
                value={connectionDraft.requiredKeys}
                placeholder="api_key"
                disabled={!canManage}
                onChange={(event) => setConnectionDraft((current) => ({
                  ...current,
                  requiredKeys: event.target.value,
                }))}
              />
            </label>
            <button
              type="button"
              disabled={
                !canManage
                || !connectionDraft.connectionId.trim()
                || !connectionDraft.resourceReference.trim()
                || !connectionDraft.principalId.trim()
                || !connectionDraft.secretReference.trim()
                || Boolean(action)
              }
              onClick={() => void createConnection()}
            >
              {action === "create-connection" ? "登记中…" : "登记连接引用"}
            </button>
          </div>
        </div>
      </details>

      <details className={styles.section}>
        <summary>
          <span>调用与结果策略</span>
          <small>
            {policy
              ? `草稿 r${policy.revision}`
              : `内置策略 · ${mcpTools.length} 个 MCP 工具已纳入模板`}
          </small>
        </summary>
        <div className={styles.sectionBody}>
          {!policy ? (
            <div className={styles.policyEmpty}>
              <p>为 <code>{policyId}</code> 创建租户覆盖后，发布版本才会进入真实 Run。</p>
              <button
                type="button"
                disabled={!canManage || Boolean(action)}
                onClick={() => void initializePolicy()}
              >
                {action === "initialize-policy" ? "初始化中…" : "初始化策略草稿"}
              </button>
            </div>
          ) : (
            <>
              {missingMcpTools.length > 0 && (
                <div className={styles.coverageWarning}>
                  <div>
                    <strong>当前策略尚未明确允许 {missingMcpTools.length} 个已绑定 MCP 工具</strong>
                    <small>
                      这会在运行时得到“no policy rule matched”；同步后仍需保存并发布策略。
                    </small>
                  </div>
                  <button
                    type="button"
                    disabled={!canManage || Boolean(action)}
                    onClick={syncMcpTools}
                  >
                    同步当前 Agent 工具
                  </button>
                </div>
              )}
              <div className={styles.ruleGroup}>
                <header>
                  <div>
                    <strong>工具调用</strong>
                    <small>无匹配规则默认拒绝；同优先级按具体度和 deny → ask → allow 决定。</small>
                  </div>
                  <button
                    type="button"
                    disabled={!canManage}
                    onClick={() => setCallRules((current) => [
                      ...current,
                      {
                        name: `rule-${current.length + 1}`,
                        tool: "*",
                        decision: "ask",
                        priority: 0,
                      },
                    ])}
                  >
                    添加规则
                  </button>
                </header>
                <div className={styles.ruleTable}>
                  {callRules.map((rule, index) => (
                    <div key={`${rule.name}-${index}`}>
                      <input
                        aria-label="调用规则名称"
                        value={rule.name}
                        disabled={!canManage}
                        onChange={(event) => updateCallRule(index, { name: event.target.value })}
                      />
                      <input
                        aria-label="工具匹配"
                        value={rule.tool ?? ""}
                        disabled={!canManage}
                        placeholder="Tool 或 glob"
                        onChange={(event) => updateCallRule(index, { tool: event.target.value })}
                      />
                      <select
                        aria-label="调用决策"
                        value={rule.decision}
                        disabled={!canManage}
                        onChange={(event) => updateCallRule(index, {
                          decision: event.target.value as StudioCallPolicyRule["decision"],
                        })}
                      >
                        <option value="allow">允许</option>
                        <option value="ask">审批</option>
                        <option value="deny">拒绝</option>
                      </select>
                      <input
                        aria-label="规则优先级"
                        type="number"
                        value={rule.priority}
                        disabled={!canManage}
                        onChange={(event) => updateCallRule(index, {
                          priority: Number(event.target.value),
                        })}
                      />
                      <button
                        type="button"
                        aria-label={`删除 ${rule.name}`}
                        disabled={!canManage}
                        onClick={() => setCallRules((current) =>
                          current.filter((_item, currentIndex) => currentIndex !== index)
                        )}
                      >
                        ×
                      </button>
                    </div>
                  ))}
                </div>
              </div>

              <div className={styles.ruleGroup}>
                <header>
                  <div>
                    <strong>工具结果</strong>
                    <small>分类只会收紧工具目录声明；不可信结果会限制后续外发和持久化。</small>
                  </div>
                  <button
                    type="button"
                    disabled={!canManage}
                    onClick={() => setResultRules((current) => [
                      ...current,
                      {
                        name: `result-${current.length + 1}`,
                        tool: "mcp__*",
                        trust: "sensitive",
                        priority: 0,
                      },
                    ])}
                  >
                    添加规则
                  </button>
                </header>
                <div className={styles.ruleTable}>
                  {resultRules.map((rule, index) => (
                    <div key={`${rule.name}-${index}`}>
                      <input
                        aria-label="结果规则名称"
                        value={rule.name}
                        disabled={!canManage}
                        onChange={(event) => updateResultRule(index, { name: event.target.value })}
                      />
                      <input
                        aria-label="结果工具匹配"
                        value={rule.tool}
                        disabled={!canManage}
                        onChange={(event) => updateResultRule(index, { tool: event.target.value })}
                      />
                      <select
                        aria-label="结果信任分类"
                        value={rule.trust}
                        disabled={!canManage}
                        onChange={(event) => updateResultRule(index, {
                          trust: event.target.value as StudioContextTrust,
                        })}
                      >
                        {Object.entries(trustLabels).map(([value, label]) => (
                          <option key={value} value={value}>{label}</option>
                        ))}
                      </select>
                      <input
                        aria-label="结果规则优先级"
                        type="number"
                        value={rule.priority}
                        disabled={!canManage}
                        onChange={(event) => updateResultRule(index, {
                          priority: Number(event.target.value),
                        })}
                      />
                      <button
                        type="button"
                        aria-label={`删除 ${rule.name}`}
                        disabled={!canManage}
                        onClick={() => setResultRules((current) =>
                          current.filter((_item, currentIndex) => currentIndex !== index)
                        )}
                      >
                        ×
                      </button>
                    </div>
                  ))}
                  {!resultRules.length && (
                    <p className={styles.empty}>没有租户结果规则；运行时仍保留工具目录声明的信任等级。</p>
                  )}
                </div>
              </div>

              <div className={styles.policyActions}>
                <div>
                  <span>{policyDirty ? "有未保存的规则修改" : "草稿与服务器一致"}</span>
                  <small>
                    {policy.publishedHash
                      ? `发布 ${policy.publishedHash.slice(0, 10)}`
                      : "尚未发布；真实 Run 继续使用平台内置策略"}
                  </small>
                </div>
                <button
                  type="button"
                  disabled={!canManage || !policyDirty || Boolean(action)}
                  onClick={() => void savePolicy()}
                >
                  {action === "save-policy" ? "保存中…" : "保存草稿"}
                </button>
                <button
                  type="button"
                  data-primary="true"
                  disabled={
                    !canManage
                    || policyDirty
                    || policy.publishedRevision === policy.revision
                    || Boolean(action)
                  }
                  onClick={() => void publishPolicy()}
                >
                  {action === "publish-policy" ? "发布中…" : "发布此 revision"}
                </button>
              </div>

              <div className={styles.simulator}>
                <header>
                  <div>
                    <strong>策略模拟器</strong>
                    <small>使用与生产完全相同的匹配器，不调用模型。</small>
                  </div>
                </header>
                <label>
                  <span>工具</span>
                  <input
                    value={simulationTool}
                    onChange={(event) => setSimulationTool(event.target.value)}
                  />
                </label>
                <label>
                  <span>当前上下文</span>
                  <select
                    value={simulationTrust}
                    onChange={(event) => setSimulationTrust(
                      event.target.value as StudioContextTrust,
                    )}
                  >
                    {Object.entries(trustLabels).map(([value, label]) => (
                      <option key={value} value={value}>{label}</option>
                    ))}
                  </select>
                </label>
                <div className={styles.simulatorButtons}>
                  <button
                    type="button"
                    disabled={policyDirty || Boolean(action)}
                    onClick={() => void simulate()}
                  >
                    {action === "simulate" ? "模拟中…" : "运行模拟"}
                  </button>
                  <button
                    type="button"
                    disabled={policyDirty || Boolean(action)}
                    onClick={() => void previewImpact()}
                  >
                    {action === "impact" ? "比较中…" : "对比已发布版本"}
                  </button>
                </div>
                {simulation && (
                  <div className={styles.simulationResult}>
                    <span data-decision={simulation.call.decision}>
                      {simulation.call.decision}
                    </span>
                    <div>
                      <strong>{simulation.call.rule_name}</strong>
                      <small>{simulation.call.reason}</small>
                    </div>
                    <span data-trust={simulation.result.trust}>
                      {trustLabels[simulation.result.trust]}
                    </span>
                    <div>
                      <strong>{simulation.result.rule_name}</strong>
                      <small>{simulation.result.reason}</small>
                    </div>
                  </div>
                )}
                {impact && (
                  <p className={styles.impact}>
                    {impact.changedCount
                      ? `${impact.changedCount}/${impact.scenarioCount} 个场景将改变`
                      : "当前场景的决策与已发布版本一致"}
                  </p>
                )}
              </div>
            </>
          )}
        </div>
      </details>

      {message && (
        <p
          className={styles.message}
          data-error={/失败|冲突|不可用|拒绝/.test(message)}
          role="status"
        >
          {message}
        </p>
      )}
    </section>
  );
}
