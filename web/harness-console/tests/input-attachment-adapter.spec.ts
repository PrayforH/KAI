import { beforeEach, describe, expect, it } from "vitest";
import type { PendingAttachment } from "@assistant-ui/react";
import {
  createInputAttachmentAdapter,
  inputAttachmentType,
  inputArtifactIdFromAttachment,
} from "../src/lib/input-attachment-adapter";
import { uploadFeedbackStore, uploadKey } from "../src/lib/upload-feedback-store";

describe("Harness input attachment adapter", () => {
  beforeEach(() => uploadFeedbackStore.clear());

  it("uploads once, then sends only the server-issued opaque id", async () => {
    let uploadedBody: FormData | undefined;
    const fetcher: typeof fetch = async (_input, init) => {
      uploadedBody = init?.body as FormData;
      return Response.json(
        {
          input_artifact_id: "input_artifact_abc123",
          name: "facts.txt",
          media_type: "text/plain",
          status: "ready",
          size_bytes: 18,
        },
        { status: 201 },
      );
    };
    const adapter = createInputAttachmentAdapter(fetcher);
    expect(adapter.accept).toBe("*");
    const file = new File(["local file content"], "facts.txt", {
      type: "text/plain",
    });

    const addition = adapter.add({ file });
    expect(Symbol.asyncIterator in addition).toBe(true);
    const states: PendingAttachment[] = [];
    for await (const state of addition as AsyncGenerator<PendingAttachment>) {
      states.push(state);
    }
    const [uploading, pending] = states;

    expect(uploading).toMatchObject({
      id: pending?.id,
      name: "facts.txt",
      status: { type: "running", reason: "uploading", progress: 0 },
    });
    expect(states).toHaveLength(2);

    expect(uploadFeedbackStore.getSnapshot()).toEqual([
      {
        key: uploadKey(file),
        fileName: "facts.txt",
        status: "ready",
      },
    ]);

    const reloadedAdapter = createInputAttachmentAdapter(fetcher);
    const complete = await reloadedAdapter.send(pending!);

    expect(uploadedBody?.get("file")).toBe(file);
    expect(pending).toMatchObject({
      type: "document",
      name: "facts.txt",
      contentType: "text/plain",
      status: { type: "requires-action", reason: "composer-send" },
    });
    expect(complete.status).toEqual({ type: "complete" });
    expect(complete.content).toEqual([
      {
        type: "file",
        data: "input_artifact_abc123",
        mimeType: "text/plain",
        filename: "facts.txt",
      },
    ]);
    expect(inputArtifactIdFromAttachment(pending)).toBe("input_artifact_abc123");
    expect(inputArtifactIdFromAttachment(complete)).toBe("input_artifact_abc123");
    expect(JSON.stringify(complete)).not.toContain("local file content");
    expect(uploadFeedbackStore.getSnapshot()).toEqual([]);
  });

  it("surfaces the Harness upload error instead of creating a broken attachment", async () => {
    const adapter = createInputAttachmentAdapter(async () =>
      Response.json(
        { error: { code: "input_artifact_too_large", message: "too large" } },
        { status: 413 },
      ),
    );

    const file = new File(["large"], "large.bin", {
      type: "application/octet-stream",
    });

    const addition = adapter.add({ file }) as AsyncGenerator<PendingAttachment>;
    const uploading = await addition.next();
    expect(uploading.value).toMatchObject({
      status: { type: "running", reason: "uploading", progress: 0 },
    });
    await expect(addition.next()).rejects.toThrow("too large");
    expect(uploadFeedbackStore.getSnapshot()).toEqual([
      {
        key: uploadKey(file),
        fileName: "large.bin",
        status: "error",
        message: "too large",
      },
    ]);
  });

  it("keeps browser images typed as images for thumbnail and preview rendering", async () => {
    expect(inputAttachmentType("image/jpeg", "01.jpg")).toBe("image");
    expect(inputAttachmentType("application/octet-stream", "scan.PNG")).toBe("image");
    expect(inputAttachmentType("application/pdf", "report.pdf")).toBe("document");

    const adapter = createInputAttachmentAdapter(async () =>
      Response.json(
        {
          input_artifact_id: "input_artifact_image",
          name: "01.jpg",
          media_type: "image/jpeg",
          status: "ready",
          size_bytes: 4,
        },
        { status: 201 },
      ),
    );
    const file = new File(["jpeg"], "01.jpg", { type: "image/jpeg" });
    const states: PendingAttachment[] = [];
    for await (const state of adapter.add({ file }) as AsyncGenerator<PendingAttachment>) {
      states.push(state);
    }

    expect(states.map((state) => state.type)).toEqual(["image", "image"]);
    expect((await adapter.send(states[1]!)).type).toBe("image");
  });
});
