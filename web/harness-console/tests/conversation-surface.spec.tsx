// @vitest-environment jsdom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { expect, it, vi } from "vitest";
import { ConversationSurfaceProvider, useAssistantSurface, type ConversationSurface } from "../src/components/assistant-bot-context";
import { PersonalAssistantEntry } from "../src/components/personal-assistant-context";
import { ConversationInputCard } from "../src/components/conversation-input-card";
import { ApprovalCard } from "../src/components/approval-card";

(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;
const input = {version: 1 as const, title: "确认范围", questions: [{id: "scope", label: "选择范围", type: "single" as const, options: ["仅网页", "全部"]}]};
function SurfaceProbe() { return <output data-assistant={useAssistantSurface()} />; }

it("uses the current surface for input and approval cards, even under an assistant entry", async () => {
  const host = document.createElement("div"); document.body.append(host);
  const root = createRoot(host); const send = vi.fn(); const decide = vi.fn();
  const render = async (surface: ConversationSurface) => act(async () => root.render(
    <PersonalAssistantEntry><ConversationSurfaceProvider surface={surface}>
      <SurfaceProbe />
      <ConversationInputCard input={input} disabled={false} onSubmit={send} />
      <ApprovalCard details={{approval_id: "approval-surface", run_id: "run", tool_name: "Bash", risk: "high", argument_summary: {command: "pwd"}}} complete={false} onDecision={decide} />
    </ConversationSurfaceProvider></PersonalAssistantEntry>
  ));
  try {
    await render("task");
    const taskCard = host.querySelector('section[aria-label="待补充的信息"]')!;
    const taskClasses = taskCard.className;
    const approval = host.querySelector('[data-approval-surface]')!;
    const taskApprovalClasses = approval.className;
    expect(host.querySelector('output')?.getAttribute('data-assistant')).toBe('false');
    expect(taskCard.textContent).toContain('1');
    await render("assistant");
    expect(host.querySelector('output')?.getAttribute('data-assistant')).toBe('true');
    expect(taskCard.className).not.toBe(taskClasses);
    expect(approval.className).not.toBe(taskApprovalClasses);
    expect(taskCard.textContent).toContain('A');
    expect(approval.textContent).toContain('较高风险');
    expect(approval.textContent).toContain('允许仅对本次调用生效');
    await render("task");
    expect(taskCard.className).toBe(taskClasses);
    expect(approval.className).toBe(taskApprovalClasses);
    expect(send).not.toHaveBeenCalled(); expect(decide).not.toHaveBeenCalled();
  } finally { await act(async () => root.unmount()); host.remove(); }
});
