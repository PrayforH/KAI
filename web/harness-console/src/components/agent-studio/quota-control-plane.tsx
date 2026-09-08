"use client";

import { type FormEvent, useEffect, useMemo, useState } from "react";
import { useAuth } from "../auth-provider";
import {
  studioClient,
  type QuotaResource,
  type StudioQuotaUsage,
} from "../../lib/studio-client";
import styles from "./quota-control-plane.module.css";

const RESOURCES: Array<{
  id: QuotaResource;
  label: string;
  description: string;
  unit: "count" | "bytes" | "tokens" | "usd";
}> = [
  { id: "concurrent_runs", label: "并发 Run", description: "当前租户同时运行的任务", unit: "count" },
  { id: "concurrent_subagents", label: "并发 Sub Agent", description: "Lead 同时委派的子任务", unit: "count" },
  { id: "active_previews", label: "活动 Preview", description: "未过期的隔离试跑环境", unit: "count" },
  { id: "mcp_requests", label: "MCP QPS", description: "当前秒的受控外部调用", unit: "count" },
  { id: "artifact_bytes", label: "制品写入", description: "本月生成的报告与文件", unit: "bytes" },
  { id: "snapshot_bytes", label: "快照写入", description: "本月保存的工作区快照", unit: "bytes" },
  { id: "deployment_promotions", label: "环境晋级", description: "本月部署与回滚操作", unit: "count" },
];

function formatValue(value: number, unit: "count" | "bytes" | "tokens" | "usd") {
  if (unit === "usd") return `$${(value / 1_000_000).toFixed(2)}`;
  if (unit === "bytes") {
    if (value >= 1024 ** 3) return `${(value / 1024 ** 3).toFixed(1)} GB`;
    if (value >= 1024 ** 2) return `${(value / 1024 ** 2).toFixed(1)} MB`;
  }
  return new Intl.NumberFormat("zh-CN", { notation: value >= 100_000 ? "compact" : "standard" }).format(value);
}

export function QuotaControlPlane() {
  const { membership } = useAuth();
  const canEdit = membership.role === "owner" || membership.role === "admin";
  const [usage, setUsage] = useState<StudioQuotaUsage | null>(null);
  const [draftLimits, setDraftLimits] = useState<Partial<Record<QuotaResource, number>>>({});
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  const globalPolicy = useMemo(
    () => usage?.policies.find((policy) => !policy.scope.agentName && !policy.scope.environment) ?? null,
    [usage],
  );

  async function load() {
    try {
      const current = await studioClient.quotaUsage();
      const policy = current.policies.find((item) => !item.scope.agentName && !item.scope.environment);
      setUsage(current);
      setDraftLimits(policy?.limits ?? {});
      setError(null);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "配额数据暂时不可用");
    }
  }

  useEffect(() => { void load(); }, []);

  async function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!globalPolicy || !canEdit) return;
    setSaving(true);
    setNotice(null);
    try {
      await studioClient.replaceQuotaPolicy(
        globalPolicy.policyId,
        globalPolicy.revision,
        draftLimits,
      );
      await load();
      setNotice("租户配额已保存，新的准入请求会立即使用该版本。");
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "配额未能保存");
    } finally {
      setSaving(false);
    }
  }

  if (!usage || !globalPolicy) {
    return <main className={styles.state} id="main-content" aria-busy={!error}><strong>{error ? "无法读取使用量" : "正在读取租户容量"}</strong><span>{error ?? "汇总准入计数器与活动 reservation…"}</span>{error && <button onClick={() => void load()}>重新加载</button>}</main>;
  }

  const counters = new Map<string, StudioQuotaUsage["counters"][number]>();
  for (const counter of usage.counters) {
    const key = `${counter.scopeKey}:${counter.resource}`;
    const current = counters.get(key);
    if (!current || counter.windowKey > current.windowKey) counters.set(key, counter);
  }

  return (
    <>
      <div className={styles.railCopy}><strong>容量与成本</strong><p>配额在 API 与 Worker 两条路径执行。页面展示的是控制面事实，不是估算值。</p></div>

      <section className={styles.content}>
        <header className={styles.header}>
          <div><p>Tenant capacity ledger</p><h1>使用量与资源准入</h1><span>查看预留、实际消耗和未知成本；修改限额不会篡改历史账本。</span></div>
          <div className={styles.unknown} data-active={usage.unknownCostEntries > 0}><small>未知成本记录</small><strong>{usage.unknownCostEntries}</strong><span>{usage.unknownCostEntries ? "需要核对模型网关回传" : "当前账本完整"}</span></div>
          <div className={styles.unknown} data-active={usage.alerts.length > 0}><small>预算告警</small><strong>{usage.alerts.length}</strong><span>{usage.alerts.length ? "已有作用域越过阈值" : "全部作用域正常"}</span></div>
        </header>

        <section className={styles.ledger} aria-label="租户配额使用量">
          {RESOURCES.map((resource) => {
            const counter = counters.get(`agent=*|environment=*:${resource.id}`);
            const limit = globalPolicy.limits[resource.id] ?? 0;
            const reserved = counter?.reserved ?? 0;
            const committed = counter?.committed ?? 0;
            const used = reserved + committed;
            const ratio = limit ? Math.min(100, (used / limit) * 100) : 0;
            return <article className={styles.meter} key={resource.id}>
              <div><strong>{resource.label}</strong><span>{resource.description}</span></div>
              <div className={styles.values}><strong>{formatValue(used, resource.unit)}</strong><span>/ {formatValue(limit, resource.unit)}</span></div>
              <div className={styles.track} role="progressbar" aria-label={resource.label} aria-valuemin={0} aria-valuemax={limit} aria-valuenow={used}><span style={{ width: `${ratio}%` }} /></div>
              <footer><span>已提交 {formatValue(committed, resource.unit)}</span><span>预留 {formatValue(reserved, resource.unit)}</span></footer>
            </article>;
          })}
        </section>

        <section className={styles.manage}>
          <div className={styles.manageIntro}><p>Admission policy</p><h2>租户默认限额</h2><span>Agent 或环境级策略可在后续版本覆盖得更严格；任何层级都不能绕过租户总量。</span></div>
          <form className={styles.form} onSubmit={save}>
            {RESOURCES.map((resource) => <label key={resource.id}><span>{resource.label}<small>{resource.id}</small></span><input type="number" min={1} step={1} disabled={!canEdit || saving} value={draftLimits[resource.id] ?? ""} onChange={(event) => setDraftLimits((current) => ({ ...current, [resource.id]: Number(event.target.value) }))} /></label>)}
            {notice && <p className={styles.notice} role="status">{notice}</p>}
            {error && <p className={styles.error} role="alert">{error}</p>}
            <div className={styles.actions}><span>{canEdit ? "保存采用 revision compare-and-set，避免覆盖他人修改。" : "当前角色可查看，但不能修改配额。"}</span><button type="submit" disabled={!canEdit || saving}>{saving ? "正在保存…" : "保存限额"}</button></div>
          </form>
        </section>

        <details className={styles.reservations}>
          <summary><span>活动 Reservation</span><strong>{usage.activeReservations.length}</strong></summary>
          <div>{usage.activeReservations.length === 0 ? <p>当前没有未结算的资源预留。</p> : usage.activeReservations.map((item) => <article key={item.reservationId}><code>{item.resource}</code><span>{item.agentName ?? "租户级"} · {item.environment ?? "全部环境"}</span><strong>{item.amount.toLocaleString("zh-CN")}</strong><small>到期 {new Date(item.expiresAt).toLocaleString("zh-CN")}</small></article>)}</div>
        </details>
        {usage.alerts.length > 0 && (
          <details className={styles.reservations} open>
            <summary><span>作用域预算告警</span><strong>{usage.alerts.length}</strong></summary>
            <div>{usage.alerts.map((item) => (
              <article key={item.alertId}>
                <code>{item.severity.toUpperCase()} · {item.resource}</code>
                <span>{item.scopeKey}</span>
                <strong>{item.usagePercent}%</strong>
                <small>阈值 {item.thresholdPercent}% · {item.windowKey}</small>
              </article>
            ))}</div>
          </details>
        )}
      </section>
    </>
  );
}
