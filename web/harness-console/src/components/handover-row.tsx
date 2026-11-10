"use client";

import { useEffect, useState } from "react";
import { handoverStore, handoversForMessage, isLatestHandover, useHandoverNotices } from "../lib/handover-store";
import { statusLead } from "../lib/handover-store";
import { assistantRequest, type Specialist } from "../lib/assistant-client";
import { pendingThreadAsk, submitThreadAnswer, type ThreadAsk } from "../lib/thread-answer";
import { ApprovalCard, type ApprovalDetails } from "./approval-card";
import { ConversationInputCard } from "./conversation-input-card";
import { loadTaskSummary } from "../lib/project-space";
import {
  grantedLocalTools,
  localToolLabel,
  pendingLocalTools,
} from "../lib/local-tool-permissions";
import { SpecialistCredentials, pendingCredentialNames } from "./specialist-credentials";
import { SpecialistSetupCard } from "./specialist-setup-card";
import { AssistantEventLine } from "./assistant-event-line";

/**
 * 转交回执属于主助理会话，而不是当前打开的任何任务线程。
 * 使用当前会话界面与 Bot 绑定，避免入口路由状态渗入历史任务消息流。
 */
export function shouldShowHandoverRows(
  assistantSurface: boolean,
  hasBot: boolean,
): boolean {
  return assistantSurface && !hasBot;
}

/**
 * 本地工具权限的一等触发：config.required_local_tools 有未授予项时，
 * 在主会话该行下展开授权卡；允许走既有 specialists/{id}/config（合并 config），
 * 暂不只在本地收起，不伪造已授予。
 */
function HandoverLocalToolPermissionCard({
  specialistId,
  specialistName,
}: {
  specialistId: string;
  specialistName: string;
}) {
  const [specialist, setSpecialist] = useState<Specialist | null>(null);
  const [settled, setSettled] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    let alive = true;
    void assistantRequest<{ specialist: Specialist }>(
      `specialists/${encodeURIComponent(specialistId)}`,
    )
      .then((data) => {
        if (alive) setSpecialist(data.specialist);
      })
      .catch(() => {
        // 权限卡是增强信息：读不到静默，任务面仍可配置。
      });
    return () => {
      alive = false;
    };
  }, [specialistId]);

  if (!specialist || settled) return null;
  const pending = pendingLocalTools(specialist);
  if (!pending.length) return null;

  async function allow() {
    if (!specialist || busy) return;
    setBusy(true);
    setError("");
    try {
      const merged = [...new Set([...grantedLocalTools(specialist), ...pending])];
      const updated = await assistantRequest<Specialist>(
        `specialists/${encodeURIComponent(specialistId)}/config`,
        "POST",
        {
          config: { ...specialist.config, local_tool_permissions: merged },
          expectedRevision: specialist.revision,
        },
      );
      setSpecialist(updated);
      window.dispatchEvent(
        new CustomEvent("kai:specialists-changed", {
          detail: { specialistId, reason: "local-tools" },
        }),
      );
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "授权失败");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="handover-local-tool-permission-card">
      <p className="handover-local-tool-title">待你授权本地能力</p>
      <ul className="handover-local-tool-list">
        {pending.map((key) => (
          <li key={key}>允许「{specialistName}」{localToolLabel(key)}吗？</li>
        ))}
      </ul>
      {error && (
        <p className="handover-local-tool-error" role="alert">
          {error}
        </p>
      )}
      <div className="handover-local-tool-actions">
        <button type="button" data-action="later" disabled={busy} onClick={() => setSettled(true)}>
          暂不
        </button>
        <button type="button" data-action="allow" disabled={busy} onClick={() => void allow()}>
          {busy ? "正在授权…" : "允许"}
        </button>
      </div>
    </div>
  );
}

/**
 * 子线程处于 waiting_approval 时，把 ApprovalCard 投影到主会话：
 * 发现走既有 thread summary 的 pending_approval，decide 走既有
 * PUT /api/harness/approvals/{id}；成功后本地收起。
 */
function HandoverApprovalCard({
  specialistId,
  specialistName,
}: {
  specialistId: string;
  specialistName: string;
}) {
  const [approval, setApproval] = useState<(ApprovalDetails & { status: string }) | null>(null);

  useEffect(() => {
    let alive = true;
    void (async () => {
      try {
        const detail = await assistantRequest<{ threadId: string | null }>(
          `specialists/${encodeURIComponent(specialistId)}`,
        );
        if (!alive || !detail.threadId) return;
        const summary = await loadTaskSummary(detail.threadId);
        if (!alive || !summary?.pending_approval) return;
        if (summary.pending_approval.status !== "pending") return;
        setApproval(summary.pending_approval);
      } catch {
        // 审批投影是增强信息：读不到静默，任务面仍有入口。
      }
    })();
    return () => {
      alive = false;
    };
  }, [specialistId]);

  if (!approval) return null;
  return (
    <div className="handover-approval-card">
      <p className="handover-approval-title">「{specialistName}」等待你审批</p>
      <ApprovalCard
        key={approval.approval_id}
        details={approval}
        complete={false}
        onDecision={async (decision) => {
          const response = await fetch(
            `/api/harness/approvals/${encodeURIComponent(approval.approval_id)}`,
            {
              method: "PUT",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({ decision }),
            },
          );
          if (!response.ok) throw new Error((await response.text()) || "审批失败");
          setApproval(null);
        }}
      />
    </div>
  );
}

/**
 * 子 Bot 线程里的 ask（harness-input）投影到主会话：读该线程最近历史，
 * 末条 assistant 仍是问卷就内联同一张 ConversationInputCard；
 * 提交直接写回该 threadId（AG-UI run），不需要点进 ?bot=。
 */
function HandoverAskCard({
  specialistId,
  specialistName,
}: {
  specialistId: string;
  specialistName: string;
}) {
  const [ask, setAsk] = useState<ThreadAsk | null>(null);
  const [target, setTarget] = useState<{
    threadId: string;
    agentName: string;
    agentVersion: string;
    agentOwnerUserId?: string;
    spaceId?: string | null;
  } | null>(null);

  useEffect(() => {
    let alive = true;
    void (async () => {
      try {
        const detail = await assistantRequest<{
          specialist: Specialist;
          threadId: string | null;
          agentName: string;
          agentVersion: string;
          agentOwnerUserId?: string;
          spaceId?: string | null;
        }>(`specialists/${encodeURIComponent(specialistId)}`);
        if (!alive || !detail.threadId) return;
        setTarget({
          threadId: detail.threadId,
          agentName: detail.agentName,
          agentVersion: detail.agentVersion,
          agentOwnerUserId: detail.agentOwnerUserId,
          spaceId: detail.spaceId,
        });
        const response = await fetch(
          `/api/agui/threads/${encodeURIComponent(detail.threadId)}/history?limit=4`,
          { cache: "no-store" },
        );
        if (!response.ok) return;
        const history = (await response.json()) as { messages?: unknown };
        if (!alive) return;
        const messages = Array.isArray(history.messages) ? history.messages : [];
        setAsk(pendingThreadAsk(messages as never));
      } catch {
        // ask 投影是增强信息：读不到就静默，不打断转交行。
      }
    })();
    return () => {
      alive = false;
    };
  }, [specialistId]);

  if (!ask || !target) return null;
  return (
    <div className="handover-ask-card">
      <p className="handover-ask-title">「{specialistName}」正在等你回答</p>
      <ConversationInputCard
        input={ask.input}
        disabled={false}
        onSubmit={async (answer) => {
          await submitThreadAnswer({ ...target, text: answer });
          setAsk(null);
        }}
      />
    </div>
  );
}

/**
 * 必填凭据的一等触发：读 specialist.config.required_credentials，
 * 只要有未满足的 credential_ref:<name>，就在主会话该行下展开内联凭据卡。
 * 全部满足后卡自动收起（不常驻），无需跳 ?bot=。
 */
function HandoverRequiredCredentials({ specialistId }: { specialistId: string }) {
  const [specialist, setSpecialist] = useState<Specialist | null>(null);

  useEffect(() => {
    let alive = true;
    void assistantRequest<{ specialist: Specialist }>(
      `specialists/${encodeURIComponent(specialistId)}`,
    )
      .then((data) => {
        if (alive) setSpecialist(data.specialist);
      })
      .catch(() => {
        // 凭据卡是增强信息：读不到就保持静默，不打断转交行。
      });
    return () => {
      alive = false;
    };
  }, [specialistId]);

  if (!specialist) return null;
  const pending = pendingCredentialNames(specialist);
  if (!pending.length) return null;
  return (
    <div className="handover-credential-card">
      <SpecialistCredentials
        specialistId={specialistId}
        specialist={specialist}
        requiredNames={pending}
        onUpdated={(next) => {
          setSpecialist(next);
          window.dispatchEvent(
            new CustomEvent("kai:specialists-changed", {
              detail: { specialistId, reason: "credential" },
            }),
          );
        }}
      />
    </div>
  );
}

/**
 * 主对话流内的转交状态行（对齐 Grok「已发送给 X」）：居中、细字、
 * 随消息滚动（非 sticky/置顶条），点击进入目标 bot 会话。
 * 数据源：派活工具的 ui.handover 自定义事件（即时）+ 页面加载时的一次性
 * handovers 投影（刷新后仍在）；无轮询。
 */
export function HandoverInlineRows({ messageId, liveRunId }: { messageId: string; liveRunId?: string }) {
  const allNotices = useHandoverNotices();
  const notices = handoversForMessage(allNotices, messageId, liveRunId);
  useEffect(() => {
    void handoverStore.seed();
  }, []);
  if (!notices.length) return null;
  return (
    <div className="handover-inline" aria-label="转交状态">
      {notices.map((notice) => {
        const current = isLatestHandover(notice, allNotices);
        // Only a confirmed execution settles this specific handover.
        const settled = () => {
          handoverStore.markConfigured(notice.handoverId);
          // rail 也立刻重拉，让该 bot 的配置与状态跟上这次转交。
          window.dispatchEvent(
            new CustomEvent("kai:specialists-changed", {
              detail: { specialistId: notice.specialistId, reason: "configured" },
            }),
          );
        };
        return (
        <div className="handover-inline-item" key={notice.handoverId}>
        <AssistantEventLine
          label={statusLead(notice.status)} name={notice.specialistName}
          botId={notice.specialistId} color={notice.appearance?.color} avatar={notice.appearance?.avatar}
          href={`/assistant?bot=${encodeURIComponent(notice.specialistId)}`}
          attention={notice.status === "failed"}
        />
          {/* Prompts belong to the latest request, never every historical receipt. */}
          {current && <>
          <HandoverLocalToolPermissionCard
            specialistId={notice.specialistId}
            specialistName={notice.specialistName}
          />
          <HandoverApprovalCard
            specialistId={notice.specialistId}
            specialistName={notice.specialistName}
          />
          {notice.status !== "failed" && (
            <HandoverAskCard
              specialistId={notice.specialistId}
              specialistName={notice.specialistName}
            />
          )}
          <HandoverRequiredCredentials specialistId={notice.specialistId} />
          {notice.status === "pending_input" && (
            // 缺配置时不必跳进 ?bot=：同一张 SpecialistSetupCard 直接在主会话里补。
            <SpecialistSetupCard
              specialistId={notice.specialistId}
              objectiveId={notice.objectiveId}
              includeCredentials={false}
              onReady={settled}
              onAlreadyActive={settled}
            />
          )}
          </>}
        </div>
        );
      })}
    </div>
  );
}
