import { renderToStaticMarkup } from "react-dom/server";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { expect, it, vi } from "vitest";

vi.mock("@assistant-ui/react-ui", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@assistant-ui/react-ui")>();
  const ThreadWelcome = Object.assign(
    ({ children }: { children?: React.ReactNode }) => <div>{children}</div>,
    {
      Root: ({ children }: { children?: React.ReactNode }) => <div>{children}</div>,
      Center: ({ children }: { children?: React.ReactNode }) => <div>{children}</div>,
      Avatar: () => <div>H</div>,
      Message: () => null,
      Suggestions: () => null,
      Suggestion: ({
        suggestion,
      }: {
        suggestion: { text?: React.ReactNode; prompt: string };
      }) => <button type="button">{suggestion.text ?? suggestion.prompt}</button>,
    },
  );
  return {
    ...actual,
    ThreadWelcome,
    Thread: (props: {
      assistantMessage?: {
        components?: {
          ToolFallback?: React.ComponentType<Record<string, unknown>>;
        };
      };
      components?: {
        ThreadWelcome?: React.ComponentType;
      };
    }) => {
      const ToolFallback = props.assistantMessage?.components?.ToolFallback;
      const Welcome = props.components?.ThreadWelcome;
      if (!ToolFallback) return <div>missing tool fallback</div>;
      return (
        <>
          {Welcome && <Welcome />}
          <ToolFallback
            toolCallId="approval-tool-1"
            toolName="harness_request_approval"
            args={{
              approval_id: "approval-1",
              run_id: "run-1",
              tool_call_id: "bash-1",
              reason: "matched policy rule bash-review",
            }}
            argsText={'{"approval_id":"approval-1"}'}
          />
        </>
      );
    },
  };
});

import {
  AgentThread,
  incompleteRunGuidance,
  inputArtifactDownloadHref,
  isIntermediateAssistantTextPart,
  messageOwnsRun,
  turnOwnsRun,
  normalizeMessageText,
  ownsLiveResponse,
  shouldOfferIncompleteRetry,
  shouldSuppressNativeAssistantText,
  shouldShowComposerStop,
  shouldShowPreResponseActivity,
} from "../src/components/agent-thread";

const agentThreadSource = readFileSync(
  join(process.cwd(), "src/components/agent-thread.tsx"),
  "utf8",
);

it("registers the approval renderer through the assistant-ui Thread config", () => {
  renderToStaticMarkup(<AgentThread userId="user-a" threadId="thread-a" />);

  expect(agentThreadSource).toContain('part.toolName === "harness_request_approval"');
  expect(agentThreadSource).toContain("<ApprovalToolBridge");
  expect(agentThreadSource).toContain('className="composer-approval-slot"');
  expect(agentThreadSource).toContain("<ApprovalCard");
  expect(agentThreadSource).toContain("runView.queueReason");
  expect(agentThreadSource).toContain("直达审批");
});

it("presents task-first guidance through a custom assistant-ui welcome", () => {
  const html = renderToStaticMarkup(<AgentThread userId="user-a" threadId="thread-a" />);

  expect(html).toContain("把目标交给智能体，让它替你完成");
  expect(html).toContain("执行过程、工具调用和产出，都会留在这段对话里");
  expect(html).not.toContain("常规操作自动完成");
  expect(html).not.toContain("隔离执行 · 自动风险分级");
  expect(html).not.toContain("分析与规划");
  expect(html).not.toContain("阅读与整理");
  expect(html).not.toContain("执行与协作");
  expect(html).not.toContain("创建或调整智能体");
  expect(html).not.toContain("支持人工审批");
});

it("uses the current run control name in incomplete-run guidance", () => {
  expect(agentThreadSource).toContain("可打开“运行详情”查看原因");
  expect(agentThreadSource).toContain("<ActionBarPrimitive.Reload");
  expect(agentThreadSource).toContain('className="run-retry-button"');
  expect(agentThreadSource).toContain("重新运行");
  expect(agentThreadSource).not.toContain("请查看运行详情");
});

it("keeps user-stopped runs neutral and only offers retry for actual failures", () => {
  expect(shouldOfferIncompleteRetry({ type: "incomplete", reason: "cancelled" })).toBe(false);
  expect(shouldOfferIncompleteRetry({ type: "incomplete", reason: "error" })).toBe(true);
  expect(shouldOfferIncompleteRetry({ type: "complete", reason: "unknown" })).toBe(false);
  expect(agentThreadSource).toContain(
    "const showIncompleteRecovery = shouldOfferIncompleteRetry(messageStatus)",
  );
  expect(agentThreadSource).toContain("{showIncompleteRecovery ? (");
  expect(agentThreadSource).not.toContain('<circle\n                cx="12"');
});

it("uses interactive answer branch primitives for regenerated responses", () => {
  expect(agentThreadSource).toContain("<HarnessBranchPicker />");
  expect(agentThreadSource).toContain("<BranchPickerPrimitive.Previous asChild>");
  expect(agentThreadSource).toContain("<BranchPickerPrimitive.Next asChild>");
  expect(agentThreadSource).toContain('aria-label="上一个回答"');
  expect(agentThreadSource).toContain('aria-label="下一个回答"');
});

it("distinguishes a historical failed turn from the current run", () => {
  expect(incompleteRunGuidance(true)).toBe(
    "本次运行未完整结束，可打开“运行详情”查看原因。",
  );
  expect(incompleteRunGuidance(false)).toBe(
    "该条历史运行未完整结束，可打开“运行详情”查看原因。",
  );
});

it("keeps implementation status out of the composer footer", () => {
  expect(agentThreadSource).not.toContain('className="composer-meta"');
  expect(agentThreadSource).not.toContain("隔离工作区");
  expect(agentThreadSource).not.toContain("未发送内容已保存在当前浏览器");
  expect(agentThreadSource).not.toContain('className="composer-stop-button"');
  expect(agentThreadSource).toContain('cancel: { tooltip: "停止运行" }');
});

it("switches the composer action to stop for an active run", () => {
  expect(shouldShowComposerStop(true, "idle")).toBe(true);
  expect(shouldShowComposerStop(false, "running")).toBe(true);
  expect(shouldShowComposerStop(false, "complete")).toBe(false);
  expect(shouldShowComposerStop(false, "error")).toBe(false);
  expect(shouldShowComposerStop(true, "running", "failed")).toBe(false);
  expect(shouldShowComposerStop(true, "running", "cancelled")).toBe(false);
  expect(agentThreadSource).toContain(
    'className="aui-button aui-button-icon aui-composer-cancel"',
  );
  expect(agentThreadSource).toContain('aria-label="停止运行"');
  expect(agentThreadSource).toContain('width="12" height="12"');
  expect(agentThreadSource).not.toContain('title="停止运行"');
  expect(agentThreadSource).toContain("aui.thread().cancelRun()");
  expect(agentThreadSource).toContain("aui-composer-send-icon");
  expect(agentThreadSource).not.toContain("<Composer.Action");
});

it("shows run activity before the first assistant message is created", () => {
  expect(shouldShowPreResponseActivity(true, "queued")).toBe(true);
  expect(shouldShowPreResponseActivity(true, "running")).toBe(true);
  expect(shouldShowPreResponseActivity(true, "waiting_approval")).toBe(true);
  expect(shouldShowPreResponseActivity(false, "running")).toBe(false);
  expect(shouldShowPreResponseActivity(true, "completed")).toBe(false);
  expect(agentThreadSource).toContain('data-activity-source="pre-response"');
  expect(agentThreadSource).toContain(
    "activity.run_id === runView?.runId",
  );
});

it("keeps assistant output avatar-free so activity rows cannot overlap it", () => {
  expect(agentThreadSource).not.toContain("<AssistantMessage.Avatar />");
  expect(agentThreadSource).not.toContain("assistantAvatar=");
});

it("places copy before edit below the user message content", () => {
  const content = agentThreadSource.indexOf("<UserMessage.Content />");
  const actions = agentThreadSource.indexOf("<ActionBarPrimitive.Root", content);
  const copy = agentThreadSource.indexOf("<MessageCopyButton", actions);
  const edit = agentThreadSource.indexOf('aria-label="编辑消息"', actions);
  expect(content).toBeGreaterThan(-1);
  expect(actions).toBeGreaterThan(content);
  expect(copy).toBeGreaterThan(actions);
  expect(edit).toBeGreaterThan(copy);
  expect(agentThreadSource).toContain("onClick={beginEdit}");
  expect(agentThreadSource).toContain("<MessageCopyButton");
  expect(agentThreadSource).toContain('aria-label="编辑消息"');
  expect(agentThreadSource).toContain('label="复制消息"');
});

it("normalizes copied message text and provides an HTTP-safe clipboard fallback", () => {
  expect(normalizeMessageText(" 第一行\r\n\r\n\r\n第二行  \n")).toBe("第一行\n\n第二行");
  expect(normalizeMessageText("标题\n　\n​\n\n正文\u2029\u2029结尾")).toBe(
    "标题\n\n正文\n\n结尾",
  );
  expect(normalizeMessageText("上海贤创广告有限公司 下钻")).toBe("上海贤创广告有限公司 下钻");
  expect(agentThreadSource).toContain('document.execCommand("copy")');
  expect(agentThreadSource).toContain('data-copy-state={copyState}');
  expect(agentThreadSource).toContain('className="message-copy-status"');
  expect(agentThreadSource).not.toContain('className="sr-only"');
});

it("removes answer regeneration while preserving failed-run recovery", () => {
  expect(agentThreadSource).toContain("allowReload: false");
  expect(agentThreadSource).not.toContain("<AssistantActionBar.Reload");
  expect(agentThreadSource).toContain("<ActionBarPrimitive.Reload");
});

it("only edits the latest user turn and allows unchanged text to start a new run", () => {
  expect(agentThreadSource).toContain('className="user-message-editor"');
  expect(agentThreadSource).toContain("MessageEditorContext.Provider");
  expect(agentThreadSource).toContain("isLatestUserMessage");
  expect(agentThreadSource).toContain("{isLatestUserMessage && !threadRunning ? (");
  expect(agentThreadSource).toContain("setEditor({ messageId: message.id, draft: originalText })");
  expect(agentThreadSource).toContain("sourceId: message.id");
  expect(agentThreadSource).toContain("startRun: true");
  expect(agentThreadSource).not.toContain("text === originalText.trim()");
  expect(agentThreadSource).toContain("发送");
});

it("places each run activity before its assistant answer", () => {
  const assistantRoot = agentThreadSource.indexOf("<AssistantMessage.Root");
  const activity = agentThreadSource.indexOf("<TurnActivity", assistantRoot);
  const content = agentThreadSource.indexOf("<AssistantMessage.Content", assistantRoot);
  expect(activity).toBeGreaterThan(assistantRoot);
  expect(content).toBeGreaterThan(activity);
  expect(agentThreadSource).toContain('className="assistant-message-controls"');
  expect(agentThreadSource).toContain('part.toolName === "harness_run_activity"');
  expect(agentThreadSource).toContain(
    "shouldKeepActivityInLatestSlot(",
  );
  expect(agentThreadSource).toContain(
    'data-activity-source={isLast ? "current-run" : "captured-turn"}',
  );
  expect(agentThreadSource).not.toContain("stream.runId,");
  expect(agentThreadSource).toContain(
    "responseStarted={!isLast || responseStarted}",
  );
  expect(agentThreadSource).not.toContain("live.runId === runView?.runId");
  expect(agentThreadSource).not.toContain("MessagesFooter: LatestActivity");
});

it("renders uploaded images in an in-app original-size preview", () => {
  expect(
    inputArtifactDownloadHref("input_artifact_123", "history-index"),
  ).toBe("/api/input-artifacts/input_artifact_123/content");
  expect(agentThreadSource).toContain("HarnessMessageAttachment");
  expect(agentThreadSource).toContain("点击下载");
  expect(agentThreadSource).toContain('data-kind={isImage ? "image" : "file"}');
  expect(agentThreadSource).toContain("message-attachment-preview");
  expect(agentThreadSource).toContain("message-attachment-open");
  expect(agentThreadSource).toContain("image-lightbox");
  expect(agentThreadSource).toContain("上传原图");
  expect(agentThreadSource).toContain("下载原图");
  expect(agentThreadSource).not.toContain('target: "_blank"');
});

it("keeps final assistant text mounted outside the live-stream handoff", () => {
  expect(agentThreadSource).toContain(
    "function HarnessAssistantText(part: TextMessagePartProps)",
  );
  expect(agentThreadSource).toContain("<MarkdownText />");
  expect(agentThreadSource).toContain("shouldSuppressNativeAssistantText(");
  expect(agentThreadSource).toContain("isIntermediateAssistantTextPart(parts, partIndex)");
});

it("projects only pre-tool assistant prose as activity commentary", () => {
  expect(
    isIntermediateAssistantTextPart(
      [
        { type: "text" },
        { type: "tool-call", toolName: "Read" },
        { type: "text" },
      ],
      0,
    ),
  ).toBe(true);
  expect(
    isIntermediateAssistantTextPart(
      [
        { type: "text" },
        { type: "tool-call", toolName: "Read" },
        { type: "text" },
      ],
      2,
    ),
  ).toBe(false);
  expect(
    isIntermediateAssistantTextPart(
      [{ type: "text" }],
      0,
    ),
  ).toBe(false);
});

it("keeps the final answer visible before durable activity projections", () => {
  const historyParts = [
    { type: "text" },
    { type: "tool-call", toolName: "Read" },
    { type: "text" },
    { type: "tool-call", toolName: "harness_run_activity" },
    { type: "tool-call", toolName: "harness_present_artifact" },
  ];

  expect(isIntermediateAssistantTextPart(historyParts, 0)).toBe(true);
  expect(isIntermediateAssistantTextPart(historyParts, 2)).toBe(false);
  expect(
    isIntermediateAssistantTextPart(
      [
        { type: "text" },
        { type: "tool-call", toolName: "harness_request_approval" },
      ],
      0,
    ),
  ).toBe(true);
});

it("uses one stable native assistant message for streaming output", () => {
  expect(agentThreadSource).toContain('className="assistant-answer"');
  expect(agentThreadSource).toContain(
    'data-streaming={part.status.type === "running" ? "true" : "false"}',
  );
  expect(agentThreadSource).not.toContain("MessagesFooter:");
  expect(agentThreadSource).toContain(
    'data-direct-stream={directStream ? "true" : "false"}',
  );
  expect(agentThreadSource).toContain(
    "<LiveAssistantResponse live={live} ownsMessage={ownsLive} />",
  );
  expect(agentThreadSource).toMatch(
    /<LiveAssistantResponse live=\{live\} ownsMessage=\{ownsLive\} \/>[\s\S]*?<AssistantMessage\.Content/,
  );
  expect(agentThreadSource).toContain("<TextMessagePartProvider");
  expect(agentThreadSource).not.toContain("hasCurrentTurnAssistantText");
});

it("does not render the native text slot while the live response owns it", () => {
  expect(shouldSuppressNativeAssistantText(true, "streaming")).toBe(true);
  expect(shouldSuppressNativeAssistantText(true, "complete")).toBe(true);
  expect(shouldSuppressNativeAssistantText(false, "streaming")).toBe(false);
  expect(shouldSuppressNativeAssistantText(true, "idle")).toBe(false);
});

it("keeps interrupted live output attached to its own regenerated branch", () => {
  expect(ownsLiveResponse(true, "assistant-2", "assistant-2")).toBe(true);
  expect(ownsLiveResponse(true, "assistant-1", "assistant-2")).toBe(false);
  expect(ownsLiveResponse(false, "assistant-2", "assistant-2")).toBe(false);
  expect(ownsLiveResponse(true, "assistant-2", undefined)).toBe(false);
});

it("keeps interrupted run activity attached to its own answer branch", () => {
  expect(messageOwnsRun("assistant-run_2", "run_2")).toBe(true);
  expect(messageOwnsRun("assistant-run_2-message_a", "run_2")).toBe(true);
  expect(messageOwnsRun("assistant-run_1-message_a", "run_2")).toBe(false);
  expect(agentThreadSource).toContain("turnOwnsRun(");
});

it("attaches a resumed run to the latest optimistic assistant turn", () => {
  expect(turnOwnsRun("__optimistic__42", "run_2", true, "run_2")).toBe(true);
  expect(turnOwnsRun("__optimistic__42", "run_2", false, "run_2")).toBe(false);
  expect(turnOwnsRun("__optimistic__42", "run_2", true, "run_3")).toBe(false);
});
