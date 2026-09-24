import type { AssistantRuntime, CompleteAttachment } from "@assistant-ui/react";
import { inputArtifactIdFromAttachment } from "./input-attachment-adapter";

/** The runtime clears the composer before its async attachment send completes. */
export async function sendComposerWithRecovery(
  composer: AssistantRuntime["thread"]["composer"],
): Promise<string | null> {
  const before = { ...composer.getState() };
  const files: CompleteAttachment[] = [];
  for (const attachment of before.attachments) {
    const id = inputArtifactIdFromAttachment(attachment);
    if (!id || !["complete", "requires-action"].includes(attachment.status.type)) {
      return attachment.status.type === "incomplete"
        ? `附件 ${attachment.name} 上传失败，请移除后重新添加；输入内容已保留。`
        : `附件 ${attachment.name} 尚未完成上传，请稍后再发送。`;
    }
    files.push({ id, name: attachment.name, type: attachment.type,
      contentType: attachment.contentType, status: { type: "complete" },
      content: [{ type: "file", data: id, filename: attachment.name,
        mimeType: attachment.contentType || "application/octet-stream" }] });
  }
  try {
    await composer.send();
    return null;
  } catch (reason) {
    const current = composer.getState();
    // Keep anything the user typed while the asynchronous send was pending.
    if (current.text !== before.text)
      composer.setText([before.text, current.text].filter(Boolean).join("\n\n"));
    for (const file of files) {
      if (!composer.getState().attachments.some(item => inputArtifactIdFromAttachment(item) === file.id))
        await composer.addAttachment(file);
    }
    return reason instanceof Error ? reason.message : "发送失败，输入和附件已保留，请重试。";
  }
}
