"use client";

import { useEffect, useState } from "react";
import { handoverStore, useHandoverNotices } from "../lib/handover-store";
import { statusLead } from "../lib/handover-store";
import { assistantRequest, type Specialist } from "../lib/assistant-client";
import { pendingThreadAsk, submitThreadAnswer, type ThreadAsk } from "../lib/thread-answer";
import { ConversationInputCard } from "./conversation-input-card";
import { SpecialistCredentials, pendingCredentialNames } from "./specialist-credentials";
import { SpecialistSetupCard } from "./specialist-setup-card";

/**
 * 转交回执属于主助理会话，而不是当前打开的任何任务线程。
 * /assistant 入口会给整页包上 PersonalAssistantEntry；任务面切回来时它仍为
 * true，所以还要结合助理面和 bot 绑定，避免回执落进历史任务消息流。
 */
export function shouldShowHandoverRows(
  personalAssistant: boolean,
  assistantSurface: boolean,
  hasBot: boolean,
): boolean {
  return personalAssistant && assistantSurface && !hasBot;
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
  } | null>(null);

  useEffect(() => {
    let alive = true;
    void (async () => {
      try {
        const detail = await assistantRequest<{
          specialist: Specialist;
          threadId: string | null;
        }>(`specialists/${encodeURIComponent(specialistId)}`);
        if (!alive || !detail.threadId) return;
        setTarget({
          threadId: detail.threadId,
          agentName: detail.specialist.agentName,
          agentVersion: detail.specialist.agentVersion,
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
export function HandoverInlineRows() {
  const notices = useHandoverNotices();
  useEffect(() => {
    void handoverStore.seed();
    const onHandover = (event: Event) => handoverStore.ingest((event as CustomEvent).detail);
    window.addEventListener("kai:handover", onHandover);
    return () => window.removeEventListener("kai:handover", onHandover);
  }, []);
  if (!notices.length) return null;
  return (
    <div className="handover-inline" aria-label="转交状态">
      {notices.map((notice) => {
        // 行态与真实配置对齐：内联保存成功、或该 Bot 早前已启用，都显示「已安排」。
        const settled = () => {
          handoverStore.markConfigured(notice.specialistId);
          // rail 也立刻重拉，把该 bot 从「待启用」移到「在岗」。
          window.dispatchEvent(
            new CustomEvent("kai:specialists-changed", {
              detail: { specialistId: notice.specialistId, reason: "configured" },
            }),
          );
        };
        return (
        <div className="handover-inline-item" key={notice.specialistId}>
          <p className="handover-inline-line">
            {/* 与 bot-rail 同判：软导航不重挂载页面，旧线程会盖住新 bot。 */}
            <a
              href={`/assistant?bot=${encodeURIComponent(notice.specialistId)}`}
              onClick={(event) => {
                event.preventDefault();
                window.location.assign(`/assistant?bot=${encodeURIComponent(notice.specialistId)}`);
              }}
            >
              {statusLead(notice.status)}
              <span className="handover-inline-target">「{notice.specialistName}」</span>
            </a>
          </p>
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
              onReady={settled}
              onAlreadyActive={settled}
            />
          )}
        </div>
        );
      })}
    </div>
  );
}
