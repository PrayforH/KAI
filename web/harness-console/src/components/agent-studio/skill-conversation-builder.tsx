"use client";
import { ConversationControl } from "../conversation-control";

import {
  useEffect,
  useRef,
  useState,
  type KeyboardEvent,
} from "react";
import type { StudioDraft, StudioSkill } from "../../lib/agent-studio";
import {
  studioClient,
  type StudioSkillConversationMessage,
} from "../../lib/studio-client";
import styles from "./skill-conversation-builder.module.css";

type SkillConversationBuilderProps = {
  agent: Pick<
    StudioDraft,
    "name" | "displayName" | "domain" | "description" | "modelRoute"
  >;
  modelLabel: string;
  currentSkill: StudioSkill;
  onApply: (skill: StudioSkill) => void;
  onClose: () => void;
};

const starterRequests = [
  "根据当前 Skill 找出缺失的触发条件、步骤和验收标准",
  "从一个具体业务场景开始创建新的 Skill",
  "把冗长说明拆成精简工作流和按需 references",
];

function errorMessage(reason: unknown) {
  return reason instanceof Error ? reason.message : "Skill 草稿生成失败";
}

export function SkillConversationBuilder({
  agent,
  modelLabel,
  currentSkill,
  onApply,
  onClose,
}: SkillConversationBuilderProps) {
  const [messages, setMessages] = useState<StudioSkillConversationMessage[]>([
    {
      role: "assistant",
      content:
        "描述这个 Skill 要处理的具体场景，最好带一个真实请求示例。我会先补齐必要信息，再生成可审阅的工作流与附加文件。",
    },
  ]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [proposal, setProposal] = useState<StudioSkill | null>(null);
  const [followUps, setFollowUps] = useState<string[]>([]);
  const composerRef = useRef<HTMLTextAreaElement>(null);
  const transcriptRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    composerRef.current?.focus();
  }, []);

  useEffect(() => {
    transcriptRef.current?.scrollTo({
      top: transcriptRef.current.scrollHeight,
    });
  }, [busy, messages]);

  async function submit(content = input) {
    const prompt = content.trim();
    if (!prompt || busy) return;
    const nextMessages = [
      ...messages,
      { role: "user" as const, content: prompt },
    ].slice(-20);
    setMessages(nextMessages);
    setInput("");
    setBusy(true);
    setError("");
    setFollowUps([]);
    try {
      const response = await studioClient.continueSkillConversation({
        modelRoute: agent.modelRoute,
        context: {
          agentName: agent.name,
          displayName: agent.displayName,
          domain: agent.domain,
          description: agent.description,
          currentSkill: proposal ?? currentSkill,
        },
        messages: nextMessages,
      });
      setMessages((current) => [
        ...current,
        { role: "assistant" as const, content: response.reply },
      ].slice(-20));
      if (response.skill) setProposal(response.skill);
      setFollowUps(response.followUpQuestions);
    } catch (reason) {
      setError(errorMessage(reason));
    } finally {
      setBusy(false);
    }
  }

  function handleComposerKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key !== "Enter" || event.shiftKey || event.nativeEvent.isComposing) {
      return;
    }
    event.preventDefault();
    void submit();
  }

  return (
    <section
      id="skill-conversation-builder"
      className={styles.builder}
      aria-label="Skill 对话共创"
    >
      <header className={styles.header}>
        <div className={styles.assistantMark} aria-hidden="true">✦</div>
        <div>
          <span>SKILL COPILOT</span>
          <strong>通过对话创建或改写 Skill</strong>
          <small>{modelLabel} · 生成结果需确认后才会写入当前草稿</small>
        </div>
        <button type="button" onClick={onClose} aria-label="关闭 Skill 对话共创">
          ×
        </button>
      </header>

      <div className={proposal ? styles.workspaceWithProposal : styles.workspace}>
        <div className={styles.conversation}>
          <div
            ref={transcriptRef}
            className={styles.transcript}
            role="log"
            aria-live="polite"
          >
            {messages.map((message, index) => (
              <article
                key={`${message.role}-${index}`}
                className={styles.message}
                data-role={message.role}
              >
                <span>{message.role === "assistant" ? "模型" : "你"}</span>
                <p>{message.content}</p>
              </article>
            ))}
            {busy && (
              <article className={styles.thinking} aria-label="模型正在整理 Skill">
                <span />
                <span />
                <span />
                <p>正在整理触发条件、工作流和资源边界…</p>
              </article>
            )}
          </div>

          {messages.length === 1 && (
            <div className={styles.starters} aria-label="建议开始方式">
              {starterRequests.map((request) => (
                <button
                  key={request}
                  type="button"
                  disabled={busy}
                  onClick={() => void submit(request)}
                >
                  {request}
                </button>
              ))}
            </div>
          )}

          {followUps.length > 0 && (
            <div className={styles.followUps}>
              <span>可以直接回答</span>
              {followUps.map((question) => (
                <button
                  key={question}
                  type="button"
                  onClick={() => {
                    setInput(question);
                    composerRef.current?.focus();
                  }}
                >
                  {question}
                </button>
              ))}
            </div>
          )}

          {error && (
            <div className={styles.error} role="alert">
              <strong>这次没有生成草稿</strong>
              <span>{error}</span>
            </div>
          )}

          <div className={`${styles.composer} harness-composer-shell`}><div className="aui-composer-root">
            <textarea
              className="aui-composer-input"
              ref={composerRef}
              rows={1}
              value={input}
              disabled={busy}
              placeholder="例如：用户上传合同后，提取付款、违约和续约条款，并输出风险表…"
              aria-label="向 Skill 共创助手发送消息"
              onChange={(event) => setInput(event.target.value)}
              onKeyDown={handleComposerKeyDown}
            />
            <footer className="composer-toolbar">
              <span>Enter 发送 · Shift + Enter 换行</span>
              <ConversationControl action="send" aria-label={busy ? "生成中" : "发送"}
                disabled={busy || !input.trim()} onClick={() => void submit()} />
            </footer>
          </div></div>
        </div>

        {proposal && (
          <aside className={styles.proposal} aria-label="待应用的 Skill 草稿">
            <header>
              <div>
                <span>READY TO REVIEW</span>
                <strong>待应用草稿</strong>
              </div>
              <em>{proposal.files?.length ?? 0} 个附加文件</em>
            </header>
            <code>{proposal.name}</code>
            <p>{proposal.description}</p>
            <div className={styles.instructionPreview}>
              <span>工作流预览</span>
              <pre>{proposal.instructions}</pre>
            </div>
            {(proposal.files?.length ?? 0) > 0 && (
              <div className={styles.fileList}>
                <span>附加文件</span>
                {proposal.files?.map((file) => (
                  <code key={file.path}>{file.path}</code>
                ))}
              </div>
            )}
            <footer className="composer-toolbar">
              <button
                type="button"
                className={styles.applyButton}
                onClick={() => onApply(proposal)}
              >
                应用到当前 Skill
              </button>
              <small>应用后仍需保存并通过发布检查</small>
            </footer>
          </aside>
        )}
      </div>
    </section>
  );
}
