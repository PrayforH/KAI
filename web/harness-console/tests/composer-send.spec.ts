import { expect, it, vi } from "vitest";
import type { AssistantRuntime } from "@assistant-ui/react";
import { sendComposerWithRecovery } from "../src/lib/composer-send";

function fixture(status = "requires-action", artifactId: string | undefined = "input_artifact_qa") {
  const state = { text: "根据附件修改配置", attachments: [{ id: "upload:qa", name: "qa.txt", type: "document", contentType: "text/plain", harnessInputArtifactId: artifactId, status: { type: status } }] };
  const composer = { getState: () => state, send: vi.fn(), setText: vi.fn((text: string) => { state.text = text; }),
    addAttachment: vi.fn(async (file) => { state.attachments.push(file); }) };
  return { state, composer, send: () => sendComposerWithRecovery(composer as unknown as AssistantRuntime["thread"]["composer"]) };
}
it.each(["running", "incomplete"])("keeps text and files when upload is %s", async status => {
  const f = fixture(status, undefined);
  expect(await f.send()).toContain("附件 qa.txt");
  expect(f.composer.send).not.toHaveBeenCalled();
  expect(f.state.text).toBe("根据附件修改配置");
  expect(f.state.attachments).toHaveLength(1);
});
it("restores server-backed files and text when adapter send rejects after clearing", async () => {
  const f = fixture();
  f.composer.send.mockImplementation(async () => { f.state.text = "刚补充的要求"; f.state.attachments = []; throw new Error("传输失败"); });
  expect(await f.send()).toBe("传输失败");
  expect(f.state.text).toBe("根据附件修改配置\n\n刚补充的要求");
  expect(f.composer.addAttachment).toHaveBeenCalledWith(expect.objectContaining({ id: "input_artifact_qa", status: { type: "complete" } }));
  f.composer.send.mockResolvedValue(undefined);
  expect(await f.send()).toBeNull();
  expect(f.composer.send).toHaveBeenCalledTimes(2);
});
it("does not duplicate content when a send rejects before clearing", async () => {
  const f = fixture();
  f.composer.send.mockRejectedValue(new Error("未发送"));
  await f.send();
  expect(f.state.text).toBe("根据附件修改配置");
  expect(f.composer.addAttachment).not.toHaveBeenCalled();
});
