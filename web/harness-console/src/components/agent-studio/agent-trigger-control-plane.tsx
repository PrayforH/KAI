"use client";

import { useEffect, useMemo, useState } from "react";
import {
  StudioApiError,
  studioClient,
  type StudioAgentTrigger,
  type StudioCreatedAgentTrigger,
  type StudioEnvironment,
  type StudioEnvironmentName,
} from "../../lib/studio-client";
import styles from "./agent-trigger-control-plane.module.css";

type AgentTriggerControlPlaneProps = {
  agentName: string;
  publishedVersion: string | null;
  environments: StudioEnvironment[];
  canManage: boolean;
  kindFilter?: "a2a";
};

function endpoint(trigger: Pick<StudioAgentTrigger, "triggerId" | "kind">) {
  const id = encodeURIComponent(trigger.triggerId);
  if (trigger.kind === "a2a") return `/a2a/agent-triggers/${id}/message:send`;
  if (trigger.kind === "chatops") return `/chatops/agent-triggers/${id}`;
  if (trigger.kind === "schedule") return "由 Worker 按计划触发";
  return `/webhooks/agent-triggers/${id}`;
}

function discoveryEndpoint(trigger: Pick<StudioAgentTrigger, "triggerId" | "kind">) {
  const id = encodeURIComponent(trigger.triggerId);
  if (trigger.kind === "a2a") return `/a2a/agent-triggers/${id}/agent-card.json`;
  if (trigger.kind === "webhook") return `/webhooks/agent-triggers/${id}/openapi.json`;
  return null;
}

export function AgentTriggerControlPlane({
  agentName,
  publishedVersion,
  environments,
  canManage,
  kindFilter,
}: AgentTriggerControlPlaneProps) {
  const isA2A = kindFilter === "a2a";
  const [triggers, setTriggers] = useState<StudioAgentTrigger[]>([]);
  const [name, setName] = useState(isA2A ? "A2A 入口" : "Webhook 入口");
  const [kind, setKind] = useState<StudioAgentTrigger["kind"]>(
    isA2A ? "a2a" : "webhook",
  );
  const [schedulePrompt, setSchedulePrompt] = useState("执行定时任务");
  const [intervalMinutes, setIntervalMinutes] = useState(60);
  const [environment, setEnvironment] =
    useState<StudioEnvironmentName>("production");
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [issued, setIssued] = useState<StudioCreatedAgentTrigger | null>(null);
  const deployedEnvironments = useMemo(
    () => environments.filter(
      (item) => item.healthySnapshotId
        && item.resourcePolicy.credentialScopes.includes("workload"),
    ),
    [environments],
  );
  const visibleTriggers = useMemo(
    () => triggers.filter((item) => isA2A ? item.kind === "a2a" : item.kind !== "a2a"),
    [isA2A, triggers],
  );
  useEffect(() => {
    if (isA2A) setKind("a2a");
  }, [isA2A]);

  useEffect(() => {
    const preferred =
      deployedEnvironments.find((item) => item.name === "production")
      ?? deployedEnvironments[0];
    if (preferred) setEnvironment(preferred.name);
  }, [deployedEnvironments]);

  useEffect(() => {
    let active = true;
    if (!publishedVersion || !agentName) {
      setTriggers([]);
      return;
    }
    studioClient
      .listTriggers(agentName)
      .then((items) => {
        if (active) setTriggers(items);
      })
      .catch((reason: unknown) => {
        if (active) {
          setError(reason instanceof Error ? reason.message : "触发器读取失败");
        }
      });
    return () => {
      active = false;
    };
  }, [agentName, publishedVersion]);

  async function createTrigger() {
    setBusy("create");
    setError("");
    try {
      const created = await studioClient.createTrigger(agentName, {
        name: name.trim(),
        environment,
        kind,
        schedule: kind === "schedule"
          ? {
              intervalSeconds: intervalMinutes * 60,
              timezone: Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC",
              prompt: schedulePrompt,
            }
          : undefined,
        chatops: kind === "chatops"
          ? { provider: "generic", allowedChannelIds: [] }
          : undefined,
      });
      setIssued(created);
      setTriggers((current) => [created.trigger, ...current]);
      setName(`${kind === "a2a" ? "A2A" : kind === "schedule" ? "定时" : kind === "chatops" ? "ChatOps" : "Webhook"} 入口`);
    } catch (reason) {
      setError(message(reason));
    } finally {
      setBusy("");
    }
  }

  async function toggle(trigger: StudioAgentTrigger) {
    setBusy(`toggle:${trigger.triggerId}`);
    setError("");
    try {
      const updated = await studioClient.updateTrigger(trigger, {
        name: trigger.name,
        enabled: !trigger.enabled,
      });
      setTriggers((current) =>
        current.map((item) =>
          item.triggerId === updated.triggerId ? updated : item,
        ),
      );
    } catch (reason) {
      setError(message(reason));
    } finally {
      setBusy("");
    }
  }

  async function rotate(trigger: StudioAgentTrigger) {
    setBusy(`rotate:${trigger.triggerId}`);
    setError("");
    try {
      const rotated = await studioClient.rotateTriggerSecret(trigger);
      setIssued(rotated);
      setTriggers((current) =>
        current.map((item) =>
          item.triggerId === rotated.trigger.triggerId
            ? rotated.trigger
            : item,
        ),
      );
    } catch (reason) {
      setError(message(reason));
    } finally {
      setBusy("");
    }
  }

  async function copy(value: string) {
    await navigator.clipboard.writeText(value);
  }

  async function copyCurl(created: StudioCreatedAgentTrigger) {
    const path = endpoint(created.trigger);
    if (!path.startsWith("/")) return;
    const url = `${window.location.origin}${path}`;
    const isA2a = created.trigger.kind === "a2a";
    const isChatOps = created.trigger.kind === "chatops";
    await copy(
      isA2a ? [
        `curl -X POST '${url.replace(/\/agent-card\.json$/, "/message:send")}' \\`,
        `  -H 'Authorization: Bearer ${created.secret}' \\`,
        "  -H 'A2A-Version: 1.0' \\",
        "  -H 'Content-Type: application/a2a+json' \\",
        `  -d '{"message":{"messageId":"your-event-id","role":"ROLE_USER","parts":[{"text":"描述要执行的任务"}]},"configuration":{"returnImmediately":true}}'`,
      ].join("\n") : isChatOps ? [
        `curl -X POST '${url}' \\`,
        `  -H 'Authorization: Bearer ${created.secret}' \\`,
        "  -H 'Content-Type: application/json' \\",
        `  -d '{"messageId":"your-event-id","channelId":"ops","actorId":"user","text":"描述要执行的任务"}'`,
      ].join("\n") : [
        `curl -X POST '${url}' \\`,
        `  -H 'Authorization: Bearer ${created.secret}' \\`,
        "  -H 'Idempotency-Key: your-event-id' \\",
        "  -H 'Content-Type: application/json' \\",
        `  -d '{"prompt":"描述要执行的任务"}'`,
      ].join("\n"),
    );
  }

  return (
    <section className={styles.controlPlane} aria-label={isA2A ? "A2A 接入" : "Agent 触发器"}>
      <header>
        <div>
          <span>{isA2A ? "AGENT-TO-AGENT" : "INVOCATION"}</span>
          <strong>{isA2A ? "A2A 接入" : "外部触发器"}</strong>
          <small>
            {isA2A
              ? "以 A2A 1.0 Agent Card 暴露能力，并复用当前环境的治理、审批与 Trace。"
              : "外部系统复用当前环境快照，并进入同一套运行、审批、取消、制品与 Trace。"}
          </small>
        </div>
        <em>{visibleTriggers.filter((item) => item.enabled).length} 个启用</em>
      </header>

      {!publishedVersion ? (
        <p className={styles.empty}>发布不可变版本后才能创建外部入口。</p>
      ) : deployedEnvironments.length === 0 ? (
        <p className={styles.empty}>
          至少需要一个健康部署、且允许“工作负载”凭据的环境。
        </p>
      ) : (
        <>
          {canManage && (
            <div className={styles.creator} data-focused={isA2A}>
              {isA2A ? (
                <div className={styles.protocolField}>
                  <span>协议</span>
                  <strong>A2A 1.0</strong>
                </div>
              ) : (
                <label>
                  <span>入口</span>
                  <select
                    value={kind}
                    onChange={(event) =>
                      setKind(event.target.value as StudioAgentTrigger["kind"])
                    }
                  >
                    <option value="webhook">Webhook</option>
                    <option value="schedule">定时</option>
                    <option value="chatops">ChatOps</option>
                  </select>
                </label>
              )}
              <label>
                <span>名称</span>
                <input
                  value={name}
                  maxLength={120}
                  onChange={(event) => setName(event.target.value)}
                />
              </label>
              <label>
                <span>环境</span>
                <select
                  value={environment}
                  onChange={(event) =>
                    setEnvironment(event.target.value as StudioEnvironmentName)
                  }
                >
                  {deployedEnvironments.map((item) => (
                    <option key={item.name} value={item.name}>
                      {item.name.toUpperCase()}
                    </option>
                  ))}
                </select>
              </label>
              <button
                type="button"
                disabled={!name.trim() || busy === "create"}
                onClick={() => void createTrigger()}
              >
                {busy === "create" ? "创建中…" : "创建入口"}
              </button>
              {kind === "schedule" && (
                <div className={styles.scheduleFields}>
                  <label>
                    <span>间隔（分钟）</span>
                    <input
                      type="number"
                      min={1}
                      value={intervalMinutes}
                      onChange={(event) => setIntervalMinutes(Number(event.target.value))}
                    />
                  </label>
                  <label>
                    <span>任务内容</span>
                    <input
                      value={schedulePrompt}
                      onChange={(event) => setSchedulePrompt(event.target.value)}
                    />
                  </label>
                </div>
              )}
            </div>
          )}

          {issued && (
            <aside className={styles.secretCard} aria-live="polite">
              <div>
                <span>只显示一次</span>
                <strong>立即保存触发密钥</strong>
                <small>关闭后无法找回，只能轮换。旧密钥在轮换后立即失效。</small>
              </div>
              <code>{issued.secret}</code>
              <div className={styles.secretActions}>
                <button type="button" onClick={() => void copy(issued.secret)}>
                  复制密钥
                </button>
                <button type="button" onClick={() => void copyCurl(issued)}>
                  复制 cURL
                </button>
                <button type="button" onClick={() => setIssued(null)}>
                  我已保存
                </button>
              </div>
            </aside>
          )}

          <div className={styles.triggerList}>
            {visibleTriggers.map((trigger) => (
              <article key={trigger.triggerId} data-enabled={trigger.enabled}>
                <span className={styles.state}>
                  {trigger.enabled ? "已启用" : "已停用"}
                </span>
                <div>
                  <strong>{trigger.name}</strong>
                  <small>
                    {trigger.kind.toUpperCase()} · {trigger.environment.toUpperCase()} · revision {trigger.revision}
                    {trigger.lastInvokedAt
                      ? ` · 最近调用 ${new Date(trigger.lastInvokedAt).toLocaleString("zh-CN")}`
                      : " · 尚未调用"}
                  </small>
                  <code>{endpoint(trigger)}</code>
                  {discoveryEndpoint(trigger) && discoveryEndpoint(trigger) !== endpoint(trigger) && (
                    <small>
                      机器发现 <code>{discoveryEndpoint(trigger)}</code>
                    </small>
                  )}
                  {trigger.nextFireAt && (
                    <small>下次执行 {new Date(trigger.nextFireAt).toLocaleString("zh-CN")}</small>
                  )}
                </div>
                <div className={styles.actions}>
                  <button
                    type="button"
                    disabled={!endpoint(trigger).startsWith("/")}
                    onClick={() => void copy(
                      `${window.location.origin}${endpoint(trigger)}`,
                    )}
                  >
                    复制地址
                  </button>
                  {discoveryEndpoint(trigger) && discoveryEndpoint(trigger) !== endpoint(trigger) && (
                    <button
                      type="button"
                      onClick={() => void copy(
                        `${window.location.origin}${discoveryEndpoint(trigger)}`,
                      )}
                    >
                      复制协议描述
                    </button>
                  )}
                  {canManage && (
                    <>
                      <button
                        type="button"
                        disabled={Boolean(busy)}
                        onClick={() => void rotate(trigger)}
                      >
                        {busy === `rotate:${trigger.triggerId}` ? "轮换中…" : "轮换密钥"}
                      </button>
                      <button
                        type="button"
                        disabled={Boolean(busy)}
                        onClick={() => void toggle(trigger)}
                      >
                        {busy === `toggle:${trigger.triggerId}`
                          ? "更新中…"
                          : trigger.enabled
                            ? "停用"
                            : "启用"}
                      </button>
                    </>
                  )}
                </div>
              </article>
            ))}
            {visibleTriggers.length === 0 && (
              <p className={styles.empty}>{isA2A ? "还没有 A2A 入口。" : "还没有外部入口。"}</p>
            )}
          </div>
        </>
      )}
      {error && <p className={styles.error}>{error}</p>}
    </section>
  );
}

function message(reason: unknown) {
  if (reason instanceof StudioApiError) return reason.message;
  return reason instanceof Error ? reason.message : "操作失败";
}
