import type {
  AttachmentAdapter,
  CompleteAttachment,
  PendingAttachment,
} from "@assistant-ui/react";
import { uploadFeedbackStore, uploadKey } from "./upload-feedback-store";
import { requireAuthenticatedResponse } from "./client-auth";

interface InputArtifactUpload {
  input_artifact_id: string;
  name: string;
  media_type: string;
  status: "ready";
  size_bytes: number;
}

type ServerBackedPendingAttachment = PendingAttachment & {
  harnessInputArtifactId?: string;
};

export function inputArtifactIdFromAttachment(value: unknown): string | null {
  if (!value || typeof value !== "object") return null;
  const attachment = value as {
    harnessInputArtifactId?: unknown;
    id?: unknown;
    content?: Array<{ type?: unknown; data?: unknown }>;
  };
  if (typeof attachment.harnessInputArtifactId === "string") {
    return attachment.harnessInputArtifactId;
  }
  const filePart = attachment.content?.find(
    (part) => part.type === "file" && typeof part.data === "string",
  );
  if (typeof filePart?.data === "string") return filePart.data;
  return typeof attachment.id === "string" && attachment.id.startsWith("input_artifact_")
    ? attachment.id
    : null;
}

function isUpload(value: unknown): value is InputArtifactUpload {
  if (!value || typeof value !== "object") return false;
  const upload = value as Record<string, unknown>;
  return (
    typeof upload.input_artifact_id === "string" &&
    typeof upload.name === "string" &&
    typeof upload.media_type === "string" &&
    upload.status === "ready" &&
    typeof upload.size_bytes === "number"
  );
}

function errorMessage(value: unknown, fallback: string) {
  if (!value || typeof value !== "object") return fallback;
  const error = (value as Record<string, unknown>).error;
  if (!error || typeof error !== "object") return fallback;
  const message = (error as Record<string, unknown>).message;
  return typeof message === "string" ? message : fallback;
}

export function inputAttachmentType(
  mediaType: string,
  name: string,
): PendingAttachment["type"] {
  const normalized = mediaType.toLowerCase();
  const extension = name.split(".").at(-1)?.toLowerCase();
  if (
    normalized.startsWith("image/")
    || ["avif", "gif", "heic", "heif", "jpeg", "jpg", "png", "webp"].includes(
      extension ?? "",
    )
  ) {
    return "image";
  }
  if (
    normalized.startsWith("text/")
    || normalized === "application/json"
    || normalized === "application/pdf"
    || ["csv", "doc", "docx", "json", "md", "pdf", "ppt", "pptx", "txt", "xls", "xlsx"].includes(
      extension ?? "",
    )
  ) {
    return "document";
  }
  return "file";
}

export function createInputAttachmentAdapter(
  fetcher: typeof fetch = fetch,
): AttachmentAdapter {
  return {
    accept: "*",
    async *add({ file }): AsyncGenerator<PendingAttachment, void> {
      const key = uploadKey(file);
      const attachmentId = `upload:${key}`;
      const initialMediaType = file.type || "application/octet-stream";
      uploadFeedbackStore.begin(key, file.name);
      yield {
        id: attachmentId,
        type: inputAttachmentType(initialMediaType, file.name),
        name: file.name,
        contentType: initialMediaType,
        file,
        status: { type: "running", reason: "uploading", progress: 0 },
      };
      try {
        const form = new FormData();
        form.append("file", file);
        const response = requireAuthenticatedResponse(
          await fetcher("/api/input-artifacts", {
            method: "POST",
            body: form,
          }),
        );
        const payload: unknown = await response.json().catch(() => null);
        if (!response.ok || !isUpload(payload)) {
          throw new Error(
            errorMessage(payload, `附件上传失败（HTTP ${response.status}）`),
          );
        }
        uploadFeedbackStore.succeed(key);
        const ready: ServerBackedPendingAttachment = {
          id: attachmentId,
          type: inputAttachmentType(payload.media_type, payload.name),
          name: payload.name,
          contentType: payload.media_type,
          file,
          status: { type: "requires-action", reason: "composer-send" },
          harnessInputArtifactId: payload.input_artifact_id,
        };
        yield ready;
      } catch (error) {
        const message = error instanceof Error ? error.message : String(error);
        uploadFeedbackStore.fail(key, message);
        throw error;
      }
    },
    async send(attachment): Promise<CompleteAttachment> {
      const artifactId = (attachment as ServerBackedPendingAttachment)
        .harnessInputArtifactId;
      if (!artifactId) {
        throw new Error(`附件 ${attachment.name} 尚未完成上传`);
      }
      const mimeType =
        attachment.contentType || attachment.file.type || "application/octet-stream";
      const complete: CompleteAttachment = {
        id: artifactId,
        type: attachment.type,
        name: attachment.name,
        contentType: mimeType,
        status: { type: "complete" },
        content: [
          {
            type: "file",
            data: artifactId,
            mimeType,
            filename: attachment.name,
          },
        ],
      };
      uploadFeedbackStore.dismiss(uploadKey(attachment.file));
      return complete;
    },
    async remove(attachment) {
      const file = "file" in attachment ? attachment.file : undefined;
      if (file) {
        uploadFeedbackStore.dismiss(uploadKey(file));
      }
      // Unbound uploads are reclaimed by the input-artifact lifecycle later.
    },
  };
}
