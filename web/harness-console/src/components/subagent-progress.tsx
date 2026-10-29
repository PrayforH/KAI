"use client";

import { useState } from "react";
import type { RunTaskNode } from "../lib/run-view-model";
import { useConversationRedactor } from "./conversation-redaction";

const roleLabels: Record<string, string> = {
  researcher: "资料研究", analyst: "分析执行", reviewer: "结果核验",
};

function stateLabel(task: RunTaskNode) {
  if (task.errorCode === "cancelled") return "已停止";
  if (task.errorCode === "completion_unconfirmed") return "未确认完成";
  return { running: "执行中", waiting: "等待审批", completed: "已完成", failed: "执行失败" }[task.status];
}

function elapsed(ms: number) {
  const seconds = Math.max(0, Math.round(ms / 1000));
  return seconds < 60 ? `${seconds}秒` : `${Math.floor(seconds / 60)}分${seconds % 60}秒`;
}

/** Durable lifecycle events, rather than the Agent launch receipt, own completion. */
export function SubagentProgress({ tasks, active }: { tasks: RunTaskNode[]; active: boolean }) {
  const [expanded, setExpanded] = useState<boolean | null>(null);
  const redact = useConversationRedactor();
  if (!tasks.length) return null;
  const running = tasks.filter(task => task.status === "running").length;
  const completed = tasks.filter(task => task.status === "completed").length;
  const failed = tasks.filter(task => task.status === "failed").length;
  const waiting = tasks.filter(task => task.status === "waiting").length;
  const open = expanded ?? (active || failed > 0);
  const counts = [running ? `${running} 个执行中` : "", waiting ? `${waiting} 个待审批` : "",
    `${completed}/${tasks.length} 已完成`, failed ? `${failed} 个异常` : ""].filter(Boolean).join(" · ");
  return <section className="subagent-progress" aria-label="子智能体协作">
    <button type="button" className="subagent-progress-heading" aria-expanded={open} onClick={() => setExpanded(!open)}>
      <span className="subagent-progress-icon" aria-hidden="true">⑂</span>
      <strong>子智能体协作</strong><span className="subagent-progress-counts" role="status">{counts}</span>
      <span aria-hidden="true">{open ? "⌃" : "⌄"}</span>
    </button>
    {open && <ul className="subagent-progress-list">
      {tasks.map(task => <li key={task.id} data-status={task.status}>
        <details className="subagent-progress-task">
          <summary>
            <span className="subagent-progress-dot" aria-hidden="true" />
            <span className="subagent-progress-copy"><strong>{redact(roleLabels[task.alias ?? ""] ?? task.alias ?? "子任务")}</strong>
              <span>{redact(task.description ?? task.title)}</span></span>
            <span className="subagent-progress-state">{stateLabel(task)}
              {task.durationMs !== undefined && <small>{elapsed(task.durationMs)}</small>}</span>
            <span className="subagent-progress-chevron" aria-hidden="true">⌄</span>
          </summary>
          <div className="subagent-progress-detail">
            <div><strong>分工</strong><p>{redact(task.description ?? task.title)}</p></div>
            {task.lastToolName && <div><strong>最近工具</strong><p>{redact(task.lastToolName)}</p></div>}
            {task.result && <div><strong>{task.status === "failed" ? "失败信息" : "返回结果"}</strong><p>{redact(task.result)}</p></div>}
            {!task.result && <p>{task.status === "running" ? "正在执行，等待返回结果。" : task.status === "completed" ? "已完成，主助手将汇总结果。" : "本次任务未返回完整结果，请查看运行详情。"}</p>}
            {task.toolUses !== undefined && <small>{task.toolUses} 次工具调用</small>}
          </div>
        </details>
      </li>)}
    </ul>}
  </section>;
}
