import { describe, expect, it } from "vitest";
import { composerTrigger, queueAttachments, queueMayDispatch, restorePromptQueue } from "../src/lib/composer-interactions";
import { composerOptions } from "../src/components/composer-assist";
import { durationLabel } from "../src/components/activity-summary";

describe("composer semantics and queue boundaries", () => {
  it("recognizes commands at input start and references at a word boundary", () => {
    expect(composerTrigger("/st", 3)?.symbol).toBe("/");
    expect(composerTrigger("请用 @研究", 6)?.query).toBe("研究");
    expect(composerTrigger("使用 $skill", 9)?.symbol).toBe("$");
    expect(composerTrigger("https://a/b", 11)).toBeNull();
    expect(composerTrigger("a@example.com", 13)).toBeNull();
    expect(composerTrigger("文件 /tmp", 7)).toBeNull();
    expect(composerOptions("/st", 3, []).map((option) => option.id)).toEqual(["/stop"]);
    expect(composerOptions("$s", 2, [], [{name:"skill-creator",description:"创建和优化技能"}])[0].id).toBe("$skill-creator");
  });
  it("only suggests skills published with the selected agent and searches descriptions", () => {
    const skills = [{ name: "risk-analysis", description: "涉非风险分析" }];
    expect(composerOptions("$风险", 3, [], skills).map((item) => item.id)).toEqual(["$risk-analysis"]);
    expect(composerOptions("$risk", 5, []).map((item) => item.id)).toEqual([]);
    expect(composerOptions("$skill", 6, [])).toEqual([]);
  });
  it("cannot dispatch while busy, awaiting approval or explicitly paused", () => {
    expect(queueMayDispatch(true, false, "completed")).toBe(false);
    expect(queueMayDispatch(false, false, "waiting_approval")).toBe(false);
    expect(queueMayDispatch(false, true, "failed")).toBe(false);
    expect(queueMayDispatch(false, false, "completed")).toBe(true);
    expect(queueMayDispatch(false, false, "queued")).toBe(false);
  });
  it("keeps only ready server attachments and never silently drops uploads", () => {
    expect(() => queueAttachments([{ id: "upload", name: "a.txt", type: "document", file: {} as File, status: { type: "running", reason: "uploading", progress: 0 } }])).toThrow("上传完成");
    const complete = queueAttachments([{ id: "input_artifact_1", name: "a.txt", type: "document", file: {} as File, status: { type: "requires-action", reason: "composer-send" } }]);
    expect(complete[0].content).toEqual([{ type: "file", data: "input_artifact_1", mimeType: "application/octet-stream", filename: "a.txt" }]);
  });
  it("restores valid queue rows without executing corrupted persisted data", () => {
    expect(restorePromptQueue("oops")).toEqual([]);
    expect(restorePromptQueue('{"text":"a"}')).toEqual([]);
    expect(restorePromptQueue('[{"id":"1","text":"a","attachments":[]}]')).toHaveLength(1);
    expect(restorePromptQueue('[{"id":"1","text":"a","attachments":[{}]}]')).toEqual([]);
  });
  it("does not show a sixty-second remainder", () => {
    expect(durationLabel(119900)).toBe("1m 59s");
  });
});
