"use client";
import { TextMessagePartProvider } from "@assistant-ui/react";
import { MarkdownText } from "../markdown-text";
import { ActivitySummary } from "../activity-summary";
import { studioClient, type StudioTryRun } from "../../lib/studio-client";
import { projectTryRunConversation } from "./try-run-stream";
import styles from "./agent-preview.module.css";

export type PreviewTurn = { prompt: string; result: StudioTryRun; files?: string[]; artifactIds?: string[] };
export function PreviewMarkdown({ text, running = false }: { text: string; running?: boolean }) {
  return <TextMessagePartProvider text={text} isRunning={running}><MarkdownText /></TextMessagePartProvider>;
}
export function PreviewRunResponse({ turn, agentName, onImprove }: { turn: PreviewTurn; agentName: string; onImprove: (turn: PreviewTurn) => void }) {
        const result = turn.result;
        const terminal = ["succeeded", "failed", "cancelled", "timed_out", "rejected"].includes(result.run.status);
        const projected = projectTryRunConversation(result.events);
        const answer = terminal ? result.finalText || projected.answerText : projected.answerText;
        const route = result.events.find(event=>event.type === "model.route.selected")?.payload;
        const status = ({ succeeded: "已完成", failed: "运行失败", cancelled: "已停止", timed_out: "已超时", rejected: "被拒绝", waiting_approval: "等待审批", queued: "排队中", provisioning: "准备中", running: "正在运行", cancelling: "正在停止" })[result.run.status];
  return           <div className={styles.answer}>
            <small className={styles.speaker}>{agentName} <span>· 试跑{!terminal || result.run.status !== "succeeded" ? ` · ${status}` : ""}</span></small>
            {route && typeof route.model === "string" && <small className={styles.speaker}>本轮模型：{route.model}{route.agent_default_route && route.route_id !== route.agent_default_route ? " · 已适配图片输入" : ""}</small>}
            {result.activity && <ActivitySummary activity={result.activity} responseStarted={Boolean(answer)} />}
            {!result.activity && projected.processText && <details className={styles.diagnostics} open={!terminal}><summary>思考与处理</summary><PreviewMarkdown text={projected.processText} running={!terminal}/></details>}
            <details className={styles.diagnostics}><summary>执行详情 · {result.events.filter(event => event.type === "tool.request").length} 次工具调用</summary>
              <p>修订 {result.draftRevision} · 执行状态不代表回答质量已通过评测。</p>
              <details><summary>本轮输入</summary><p>{turn.prompt}</p>{turn.files?.map((name, index) => <small key={index}>{name}</small>)}</details>
              <details><summary>原始事件 · {result.events.length}</summary>{result.events.map(event => <details key={event.sequence}><summary>{event.sequence} · {event.type}</summary><pre>{JSON.stringify(event.payload, null, 2)}</pre></details>)}</details>
            </details>
            {result.approvals.filter(item => item.status === "pending").map(item => <div key={item.approval_id}><p>{item.tool_name}：{item.reason}</p><button type="button" onClick={() => void studioClient.decideTryRunApproval(item.approval_id, "approved")}>批准</button><button type="button" onClick={() => void studioClient.decideTryRunApproval(item.approval_id, "rejected")}>拒绝</button></div>)}
            {answer && <PreviewMarkdown text={answer} running={!terminal} />}
            {terminal && !answer && <p>{result.run.status === "succeeded" ? "本轮已结束，未返回文字。可查看交付文件和执行详情。" : `本轮未完成。${result.run.error_code || "请查看执行详情后重试。"}`}</p>}
            {result.artifacts.filter(item => item.status === "ready").map(item => <a key={item.artifact_id} href={studioClient.tryRunArtifactHref(item.artifact_id)} download={item.name}>{item.name}</a>)}
            {terminal && <button type="button" className={styles.improve} onClick={() => onImprove(turn)}>改进这次回答</button>}
          </div>;
}
