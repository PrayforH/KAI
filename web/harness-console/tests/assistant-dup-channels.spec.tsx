import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { progressPhrase } from "../src/components/grok-activity";

const read = (path: string) => readFileSync(join(process.cwd(), path), "utf8");

describe("activity phrase never repeats the answer", () => {
  const longAnswer = "莫高窟开在敦煌城东南鸣沙山东麓的崖壁上。公元366年，僧人乐僔行经此地，见三危山金光如佛现形，于是在崖壁上开下第一个洞窟。".repeat(3);

  it("ignores message.delta items even though they carry the prose as summary", () => {
    // 后端把每条 message.delta 记成 activity item（activity.py:516），summary 就是正文。
    const activity = {
      items: [
        { event_type: "runtime.system", summary: "正在生成回复" },
        { event_type: "message.start", summary: longAnswer },
        { event_type: "message.delta", summary: longAnswer },
        { event_type: "message.completed", summary: longAnswer },
      ],
    };
    expect(progressPhrase(activity)).toBe("正在处理你的请求。");
  });

  it("returns empty when the turn only has prose (no genuine progress)", () => {
    const activity = { items: [{ event_type: "message.delta", summary: longAnswer }] };
    expect(progressPhrase(activity)).toBe("");
    // 渲染层据此整行不出现，而不是画一段正文在脸旁边。
    expect(read("src/components/agent-thread.tsx")).toContain(
      "if (!shouldShowChatActivityRow(displayed.status, displayed)) return null;",
    );
  });

  it("skips reasoning and over-long summaries", () => {
    const activity = {
      items: [
        { event_type: "reasoning.delta", summary: "思考中" },
        { event_type: "tool.request", summary: "x".repeat(120) },
        { event_type: "tool.result", summary: "已读取文件" },
      ],
    };
    expect(progressPhrase(activity)).toBe("已读取文件");
  });

  it("caps a public summary without discarding its business context", () => {
    const activity = {items: [{event_type: "reasoning.summary.delta", summary: "正在核对原任务的执行结果与文件交付。".repeat(30)}]};
    const text = progressPhrase(activity);
    expect(text).toContain("正在核对原任务");
    expect(Array.from(text).length).toBeLessThanOrEqual(96);
  });
});

describe("presentation text fallback does not reprint the answer", () => {
  it("suppresses fallback text when the turn already has answer prose", () => {
    const host = read("src/components/intelligent-ui/presentation-host.tsx");
    expect(host).toContain("suppressText?: boolean");
    expect(host).toContain('return suppressText ? null : <p>{presentation.fallback_text}</p>;');
    const thread = read("src/components/agent-thread.tsx");
    expect(thread).toContain("suppressText={Boolean(copyText.trim())}");
  });
});

describe("artifact capsules stay metadata-only", () => {
  it("file cards render name/size/preview and never the artifact body", () => {
    const results = read("src/components/assistant-results.tsx");
    expect(results).toContain("formatBytes(file.size_bytes)");
    expect(results).toContain("预览");
    // 正文只在用户点开预览后由 RailFilePreview 按需加载。
    expect(results).toContain("<RailFilePreview");
    expect(results).not.toContain("dangerouslySetInnerHTML");
  });

  it("the summary row shows thumbs + count, not prose", () => {
    const thread = read("src/components/agent-thread.tsx");
    expect(thread).toContain("artifact-summary-thumbs");
    expect(thread).toContain("countLabel");
  });
});
