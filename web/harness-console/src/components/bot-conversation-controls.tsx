"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { assistantRequest, type Specialist } from "../lib/assistant-client";
import { isSensitiveQuestion } from "../lib/conversation-input";
import { pendingThreadAsk, type HistoryMessage, type ThreadAsk } from "../lib/thread-answer";
import { handoverStore, useHandoverNotices } from "../lib/handover-store";
import { loadTaskSummary } from "../lib/project-space";
import { ApprovalCard, type ApprovalDetails } from "./approval-card";
import { ConversationInputCard } from "./conversation-input-card";
import { SpecialistCredentials, credentialNames, pendingCredentialNames } from "./specialist-credentials";
import styles from "./bot-conversation-controls.module.css";
import { credentialRequest } from "../lib/credential-request";

type Target = { specialistId: string; specialistName: string; runId: string; threadId: string; sourceThreadId: string };

/** Project an owned Bot's current interaction; answering stays on the originating timeline. */
export function BotConversationControls(target: Target) {
  const { specialistId, specialistName, runId, threadId, sourceThreadId } = target;
  const deliveries = useHandoverNotices();
  const latestKnownRun = deliveries.filter(item => item.targetThreadId === threadId)
    .reduce<(typeof deliveries)[number] | undefined>((latest, item) =>
      !latest || item.createdAt > latest.createdAt ? item : latest, undefined)?.runId;
  const [ask, setAsk] = useState<ThreadAsk | null>(null);
  const [bot, setBot] = useState<Specialist | null>(null);
  const [approval, setApproval] = useState<(ApprovalDetails & {status: string}) | null>(null);
  const [error, setError] = useState("");
  const [settled, setSettled] = useState(false);
  const requestKey = useRef<string | null>(null);
  const acceptedText = useRef<string | null>(null);

  const refresh = useCallback(async (signal: AbortSignal) => {
    try {
      const [response, summary, detail] = await Promise.all([
        fetch(`/api/agui/threads/${encodeURIComponent(threadId)}/history?limit=4`, {cache: "no-store", signal}),
        loadTaskSummary(threadId),
        assistantRequest<{specialist: Specialist}>(`specialists/${encodeURIComponent(specialistId)}`),
      ]);
      if (!response.ok) throw new Error("暂时无法读取子助理的问题");
      const history = await response.json() as {run_id?: string; messages?: HistoryMessage[]};
      if (signal.aborted) return;
      const current = (summary?.run_id ?? history.run_id) === runId;
      setBot(detail.specialist);
      setAsk(current ? pendingThreadAsk(history.messages ?? []) : null);
      setApproval(current && summary?.pending_approval?.status === "pending" ? summary.pending_approval : null);
      if (!current) setSettled(true);
      setError("");
    } catch (cause) {
      if (!signal.aborted) setError(cause instanceof Error ? cause.message : "读取失败，请重试");
    }
  }, [threadId, specialistId, runId]);

  useEffect(() => {
    if (latestKnownRun && latestKnownRun !== runId) {
      setSettled(true);
      return;
    }
    const controller = new AbortController();
    void refresh(controller.signal);
    const update = () => void refresh(controller.signal);
    window.addEventListener("focus", update);
    window.addEventListener("kai:specialists-changed", update);
    return () => { controller.abort(); window.removeEventListener("focus", update); window.removeEventListener("kai:specialists-changed", update); };
  }, [refresh, latestKnownRun, runId]);

  async function answer(text: string) {
    // A network retry reuses the accepted operation rather than making a second child Run.
    if (acceptedText.current !== null && acceptedText.current !== text) throw new Error("请先重试提交原回答，或刷新确认是否已送达");
    acceptedText.current = text;
    requestKey.current ??= crypto.randomUUID();
    const receipt = await assistantRequest<{handoverId: string; specialistId: string; createdAt: string; sourceThreadId: string; targetThreadId: string; runId: string}>(
      `specialists/${encodeURIComponent(specialistId)}/messages`, "POST",
      {text, sourceThreadId, replyToRunId: runId}, requestKey.current,
    );
    handoverStore.ingestMention({...receipt, specialistName});
    setSettled(true);
  }

  if (settled) return null;
  const secrets = ask?.input.questions.filter(isSensitiveQuestion) ?? [];
  const ordinary = ask?.input.questions.filter(question => !isSensitiveQuestion(question)) ?? [];
  const required = [...new Set([...secrets.map(question => question.id), ...(bot ? pendingCredentialNames(bot) : [])])];
  const missing = required.filter(name => !bot || !credentialNames(bot).includes(name));
  const credentialReply = secrets.filter(question => bot && credentialNames(bot).includes(question.id)).map(question => `${question.label}：已通过加密凭证通道保存，引用名称 ${question.id}`).join("\n");
  if (!ask && !approval && !missing.length && !error) return null;
  return <div className={styles.controls} aria-label={`${specialistName}的待办`}>
    {error && <div role="alert">{error}<button type="button" onClick={() => void refresh(new AbortController().signal)}>重试</button></div>}
    {approval && <ApprovalCard key={approval.approval_id} details={approval} complete={false} onDecision={async decision => {
      const response = await fetch(`/api/harness/approvals/${encodeURIComponent(approval.approval_id)}`, {
        method: "PUT", headers: {"Content-Type": "application/json"}, body: JSON.stringify({decision}),
      });
      if (!response.ok) throw new Error("授权未成功，请重试");
      setApproval(null);
    }} />}
    {bot && missing.length > 0 && <SpecialistCredentials specialistId={specialistId} specialist={bot}
      requiredNames={missing} requests={missing.map(name => credentialRequest(name, secrets.find(question => question.id === name)))}
      onUpdated={updated => { setBot(updated); }} />}
    {ask && ordinary.length > 0 && <ConversationInputCard key={ask.messageId}
      input={{...ask.input, questions: ordinary}} disabled={false}
      onSubmit={text => answer([text, credentialReply].filter(Boolean).join("\n\n"))} />}
    {ask && secrets.length > 0 && ordinary.length === 0 && !missing.length &&
      <button type="button" className={styles.continue} onClick={() => void answer(credentialReply).catch(cause => setError(String(cause)))}>凭证已保存，继续</button>}
  </div>;
}
