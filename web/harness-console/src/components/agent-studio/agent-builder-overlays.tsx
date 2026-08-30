"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import {
  apiDraftToStudioDraft,
  studioClient,
  type StudioSolidifiedAgentResult,
  type StudioTaskDrivenRecommendation,
  type StudioTryRun,
} from "../../lib/studio-client";
import { createRandomId } from "../../lib/random-id";
import type { StudioDraft } from "../../lib/agent-studio";
import styles from "./agent-builder-overlays.module.css";

export type CreatedAgentFlow = {
  draft: StudioDraft;
  prompt: string;
  recommendation: StudioTaskDrivenRecommendation | null;
  autoRun: boolean;
};

export function NewAgentDialog({
  open,
  onClose,
  onCreated,
  templates,
}: {
  open: boolean;
  onClose: () => void;
  onCreated: (flow: CreatedAgentFlow) => void;
  templates: Array<{
    template: StudioDraft["template"];
    label: string;
    description: string;
  }>;
}) {
  const [mode, setMode] = useState<"task" | "template">("task");
  const [task, setTask] = useState("");
  const [selectedTemplate, setSelectedTemplate] = useState("");
  const [agentId, setAgentId] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [domain, setDomain] = useState("");
  const [description, setDescription] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  if (!open) return null;

  async function createFromTask() {
    if (task.trim().length < 2) return;
    setBusy(true);
    setError("");
    try {
      const created = await studioClient.createDraftFromTask({
        task: task.trim(),
        runtimePreference: "auto",
      });
      onCreated({
        draft: apiDraftToStudioDraft(created.draft),
        prompt: task.trim(),
        recommendation: created.recommendation,
        autoRun: true,
      });
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "创建失败");
    } finally {
      setBusy(false);
    }
  }

  async function createFromTemplate() {
    const template = templates.find((item) => item.template === selectedTemplate);
    if (!template || agentId.trim().length < 2) return;
    setBusy(true);
    setError("");
    try {
      const created = await studioClient.createDraft({
        name: agentId.trim(),
        domain: domain.trim(),
        displayName: displayName.trim() || template.label,
        description: description.trim(),
        template: template.template,
      });
      onCreated({
        draft: apiDraftToStudioDraft(created),
        prompt: "",
        recommendation: null,
        autoRun: false,
      });
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "创建失败");
    } finally {
      setBusy(false);
    }
  }

  return <div className={styles.backdrop} role="presentation">
    <section className={`${styles.dialog} ${styles.builderDialog}`} role="dialog" aria-modal="true" aria-labelledby="new-agent-title">
      <header><div><span>NEW AGENT</span><h2 id="new-agent-title">{mode === "task" ? "描述任务，直接开始试跑" : "从服务端模板新建"}</h2></div><button type="button" onClick={onClose}>×</button></header>
      <div className={styles.modeTabs} role="tablist" aria-label="新建方式">
        <button
          type="button"
          role="tab"
          aria-selected={mode === "task"}
          onClick={() => { setMode("task"); setError(""); }}
        >任务直建</button>
        <button
          type="button"
          role="tab"
          aria-selected={mode === "template"}
          disabled={templates.length === 0}
          title={templates.length === 0 ? "能力目录暂无可用模板" : undefined}
          onClick={() => { setMode("template"); setError(""); }}
        >服务端模板</button>
      </div>
      {mode === "task" ? <>
        <p>名称、运行时和基础权限由控制面自动生成。外部 MCP 不会根据关键词自动接入。</p>
        <div className={styles.builderLayout}>
          <div className={styles.taskBrief}>
            <label><span>你希望它完成什么？</span><textarea autoFocus value={task} onChange={(event) => setTask(event.target.value)} placeholder="例如：整理指定公司的公开信息，给出投资风险摘要和证据链接。" /></label>
          </div>
          <aside className={styles.builderPromise}>
            <span>创建后会自动完成</span>
            <ol><li>生成任务契约与身份</li><li>匹配模型和最小工具权限</li><li>生成基础评测</li><li>启动隔离试跑</li><li>记录可观测执行轨迹</li></ol>
            <p>只有成功试跑才能固化为不可变版本。</p>
          </aside>
        </div>
      </> : <>
        <p>脚手架来自能力目录模板；Builder 会直接进入服务端生成的草稿，不会覆盖模板内容。</p>
        <div className={styles.builderLayout}>
          <div className={styles.templatePane}>
            <div className={styles.templateList} role="radiogroup" aria-label="选择模板">
              {templates.map((template) => (
                <button
                  key={template.template}
                  type="button"
                  role="radio"
                  aria-checked={selectedTemplate === template.template}
                  className={selectedTemplate === template.template ? styles.templateCardActive : styles.templateCard}
                  onClick={() => {
                    setSelectedTemplate(template.template);
                    setDisplayName((current) => current || template.label);
                  }}
                >
                  <strong>{template.label}</strong>
                  <small>{template.description}</small>
                </button>
              ))}
            </div>
            <div className={styles.builderFields}>
              <label><span>Agent ID</span><input autoFocus className={styles.monoField} value={agentId} onChange={(event) => setAgentId(event.target.value)} placeholder="小写字母、数字或连字符" /></label>
              <label><span>显示名称</span><input value={displayName} onChange={(event) => setDisplayName(event.target.value)} placeholder="对外展示的名称" /></label>
              <label><span>业务领域</span><input value={domain} onChange={(event) => setDomain(event.target.value)} placeholder="例如 accounts-payable" /></label>
              <label><span>场景说明</span><textarea rows={2} value={description} onChange={(event) => setDescription(event.target.value)} placeholder="它负责什么边界？可选。" /></label>
            </div>
          </div>
          <aside className={styles.builderPromise}>
            <span>模板创建的行为</span>
            <ol><li>使用服务端脚手架生成草稿</li><li>保留模板 Prompt、Skills 与工具上限</li><li>进入五阶段 Builder 的目标与契约</li><li>试跑与发布仍走既有门禁</li></ol>
            <p>前端默认草稿不会覆盖服务端脚手架。</p>
          </aside>
        </div>
      </>}
      {error && <p className={styles.error}>{error}</p>}
      <footer>
        <button type="button" onClick={onClose}>取消</button>
        {mode === "task"
          ? <button type="button" className={styles.primary} disabled={busy || task.trim().length < 2} onClick={() => void createFromTask()}>{busy ? "正在创建…" : "创建并试跑"}</button>
          : <button
              type="button"
              className={styles.primary}
              disabled={busy || !selectedTemplate || agentId.trim().length < 2}
              onClick={() => void createFromTemplate()}
            >{busy ? "正在创建…" : "从模板创建"}</button>}
      </footer>
    </section>
  </div>;
}

export function TryRunPanel({
  open,
  draft,
  initialPrompt,
  autoStart,
  recommendation,
  canSolidify,
  onClose,
  onSolidified,
}: {
  open: boolean;
  draft: StudioDraft;
  initialPrompt: string;
  autoStart: boolean;
  recommendation: StudioTaskDrivenRecommendation | null;
  canSolidify: boolean;
  onClose: () => void;
  onSolidified: (result: StudioSolidifiedAgentResult) => void;
}) {
  const [prompt, setPrompt] = useState(initialPrompt);
  const [result, setResult] = useState<StudioTryRun | null>(null);
  const [solidified, setSolidified] = useState<StudioSolidifiedAgentResult | null>(null);
  const [busy, setBusy] = useState(false);
  const [solidifying, setSolidifying] = useState(false);
  const [collapsed, setCollapsed] = useState(false);
  const [error, setError] = useState("");
  const autoRunRef = useRef("");
  const terminal = useMemo(
    () => result ? ["cancelled", "succeeded", "failed", "timed_out", "rejected"].includes(result.run.status) : false,
    [result],
  );

  useEffect(() => {
    if (!open) return;
    setPrompt(initialPrompt);
    setResult(null);
    setSolidified(null);
    setCollapsed(false);
    setError("");
    const key = `${draft.id}:${initialPrompt}`;
    if (autoStart && initialPrompt.trim() && autoRunRef.current !== key) {
      autoRunRef.current = key;
      void startRun(initialPrompt);
    }
  // A newly created flow owns this reset; draft publication updates must not restart it.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, draft.id, initialPrompt, autoStart]);

  useEffect(() => {
    if (!open || !result || terminal) return;
    const timer = window.setInterval(() => {
      void studioClient
        .getTryRun(draft.id, result.draftRevision, result.run.run_id)
        .then(setResult)
        .catch((reason) => setError(reason instanceof Error ? reason.message : "刷新失败"));
    }, 1200);
    return () => window.clearInterval(timer);
  }, [open, result, terminal, draft.id]);
  if (!open) return null;

  async function startRun(value: string) {
    setBusy(true);
    setError("");
    setResult(null);
    setSolidified(null);
    try {
      setResult(await studioClient.createTryRun(
        draft.id,
        draft.revision,
        value,
        `studio-try-${createRandomId()}`,
      ));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "试跑失败");
    } finally {
      setBusy(false);
    }
  }

  async function decide(id: string, decision: "approved" | "rejected") {
    await studioClient.decideTryRunApproval(id, decision);
    if (result) {
      setResult(await studioClient.getTryRun(
        draft.id,
        result.draftRevision,
        result.run.run_id,
      ));
    }
  }

  async function solidify() {
    if (!result || result.run.status !== "succeeded" || solidifying) return;
    setSolidifying(true);
    setError("");
    try {
      const next = await studioClient.solidifyTryRun(
        draft.id,
        result.draftRevision,
        result.draftRevision,
        result.run.run_id,
      );
      setSolidified(next);
      onSolidified(next);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "固化失败");
    } finally {
      setSolidifying(false);
    }
  }

  const traces = result?.events.filter((item) => [
    "tool.request",
    "tool.result",
    "tool.allowed",
    "tool.denied",
  ].includes(item.type)) ?? [];
  const statusLabel = result?.run.status === "succeeded"
    ? "试跑成功"
    : result?.run.status === "failed"
      ? "试跑失败"
      : result?.run.status ?? "未运行";

  return <aside className={`${styles.tryPanel} ${collapsed ? styles.tryPanelCollapsed : ""}`} aria-label="执行轨迹试跑">
    <button
      type="button"
      className={styles.tryPanelToggle}
      aria-label={collapsed ? "展开试跑面板" : "收起试跑面板"}
      aria-expanded={!collapsed}
      aria-controls="try-run-panel-body"
      onClick={() => setCollapsed((value) => !value)}
    >
      <svg viewBox="0 0 12 20" aria-hidden="true">
        <path d={collapsed ? "m9 3-6 7 6 7" : "m3 3 6 7-6 7"} />
      </svg>
    </button>
    <div className={styles.tryPanelBody} id="try-run-panel-body" aria-hidden={collapsed || undefined}>
    <header><div><span>EXECUTION TRACE</span><h2>真实试跑与版本固化</h2></div><button type="button" aria-label="关闭试跑面板" onClick={onClose}>×</button></header>
    <p><code>{draft.name}@r{result?.draftRevision ?? draft.revision}</code> · 计划、工具、修正、验证都来自真实运行事件；不会展示模型隐藏推理。</p>
    {recommendation && <section className={styles.recommendationStrip}>
      <div><span>CONTROL PLANE DECISION</span><strong>{recommendation.runtime}</strong><small>{recommendation.modelRouteId} / {recommendation.model}</small></div>
      <div><strong>{recommendation.builtinTools.length} Tools</strong><small>{recommendation.builtinTools.join(" · ") || "无"}</small></div>
      <div><strong>{recommendation.mcpServers.length} MCP</strong><small>{recommendation.mcpServers.join(" · ") || "未扩大外部边界"}</small></div>
    </section>}
    <label><span>测试任务</span><textarea value={prompt} onChange={(event) => setPrompt(event.target.value)} placeholder="输入一个能验证该 Agent 的真实任务…" /></label>
    <div className={styles.runActions}>
      <button type="button" className={styles.primary} disabled={busy || !prompt.trim() || Boolean(result && !terminal)} onClick={() => void startRun(prompt)}>{busy ? "启动隔离运行…" : result && terminal ? "重新试跑" : "运行草稿"}</button>
      {result && !terminal && <button type="button" onClick={() => void studioClient.cancelTryRun(result.run.run_id)}>取消</button>}
      <strong data-status={result?.run.status ?? "idle"}>{statusLabel}</strong>
    </div>
    {error && <p className={styles.error}>{error}</p>}
    {result && <div className={styles.runResult}>
      <section className={styles.loopSection}>
        <div className={styles.sectionTitle}><div><span>OBSERVABLE EXECUTION</span><h3>执行阶段</h3></div><small>{result.run.run_id.slice(0, 12)}</small></div>
        <ol className={styles.loopRail}>{result.loop.map((stage, index) => <li key={stage.id} data-status={stage.status}>
          <div className={styles.loopMarker}><span>{index + 1}</span></div>
          <div className={styles.loopContent}><div><strong>{stage.label}</strong><em>{stage.status}</em></div><p>{stage.summary}</p>{stage.evidence.length > 0 && <details><summary>{stage.evidence.length} 条运行证据</summary><ul>{stage.evidence.map((item) => <li key={`${stage.id}-${item.sequence}-${item.eventType}`}><code>{item.sequence}</code><span>{item.summary}</span></li>)}</ul></details>}</div>
        </li>)}</ol>
      </section>
      <section className={styles.finalResult}><h3>结果</h3><pre>{result.finalText || "等待模型输出…"}</pre>
        {result.run.status === "succeeded" && !solidified && <div className={styles.solidifyCallout}><div><strong>本次试跑已通过验证</strong><p>固化后得到不可覆盖的 <code>{draft.name}@{draft.version}</code>、Agent Bundle 和 required Eval Dataset。</p></div><button type="button" className={styles.primary} disabled={!canSolidify || solidifying} onClick={() => void solidify()}>{solidifying ? "正在固化…" : canSolidify ? `固化为 ${draft.name}@${draft.version}` : "需要 Owner / Admin 固化"}</button></div>}
        {solidified && <div className={styles.solidified}><span>IMMUTABLE AGENT</span><strong>{solidified.version.name}@{solidified.version.version}</strong><small>评测基线 {solidified.dataset.datasetId}@{solidified.dataset.version} · revision {solidified.dataset.sourceDraftRevision}</small></div>}
      </section>
      <details className={styles.evidenceGroup}><summary>工具与原始运行证据 <small>{traces.length}</small></summary>{traces.map((item) => <details key={item.event_id}><summary>{item.sequence} · {item.type}</summary><pre>{JSON.stringify(item.payload, null, 2)}</pre></details>)}{traces.length === 0 && <p>本次任务未触发工具。</p>}</details>
      {result.approvals.length > 0 && <section><h3>审批 <small>{result.approvals.length}</small></h3>{result.approvals.map((item) => <div className={styles.approval} key={item.approval_id}><div><strong>{item.tool_name ?? "工具审批"}</strong><small>{item.reason}</small></div><em>{item.status}</em>{item.status === "pending" && <><button type="button" onClick={() => void decide(item.approval_id, "approved")}>批准</button><button type="button" onClick={() => void decide(item.approval_id, "rejected")}>拒绝</button></>}</div>)}</section>}
      {result.artifacts.length > 0 && <section><h3>产物 <small>{result.artifacts.length}</small></h3>{result.artifacts.map((item) => <a key={item.artifact_id} href={studioClient.tryRunArtifactHref(item.artifact_id)} download={item.name}><span>{item.name}</span><small>{item.media_type} · {item.size_bytes ?? 0} bytes</small></a>)}</section>}
    </div>}
    </div>
  </aside>;
}
