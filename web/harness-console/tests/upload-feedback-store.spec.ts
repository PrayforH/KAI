import { beforeEach, describe, expect, it, vi } from "vitest";
import { uploadFeedbackStore } from "../src/lib/upload-feedback-store";

describe("upload feedback store", () => {
  beforeEach(() => uploadFeedbackStore.clear());

  it("tracks independent uploads and replaces a failed retry", () => {
    const listener = vi.fn();
    const unsubscribe = uploadFeedbackStore.subscribe(listener);

    uploadFeedbackStore.begin("a:3:1", "a.txt");
    uploadFeedbackStore.begin("b:4:2", "b.txt");
    uploadFeedbackStore.fail("a:3:1", "网络不可用");

    expect(uploadFeedbackStore.getSnapshot()).toEqual([
      { key: "a:3:1", fileName: "a.txt", status: "error", message: "网络不可用" },
      { key: "b:4:2", fileName: "b.txt", status: "uploading" },
    ]);

    uploadFeedbackStore.begin("a:3:1", "a.txt");
    uploadFeedbackStore.succeed("a:3:1");

    expect(uploadFeedbackStore.getSnapshot()).toEqual([
      { key: "a:3:1", fileName: "a.txt", status: "ready" },
      { key: "b:4:2", fileName: "b.txt", status: "uploading" },
    ]);
    expect(listener).toHaveBeenCalledTimes(5);
    unsubscribe();
  });

  it("dismisses one notice without clearing other uploads", () => {
    uploadFeedbackStore.begin("a", "a.txt");
    uploadFeedbackStore.begin("b", "b.txt");

    uploadFeedbackStore.dismiss("a");

    expect(uploadFeedbackStore.getSnapshot()).toEqual([
      { key: "b", fileName: "b.txt", status: "uploading" },
    ]);
  });

  it("reports transfer progress only while the upload is running", () => {
    uploadFeedbackStore.begin("a:3:1", "a.png");
    uploadFeedbackStore.progress("a:3:1", 42.4);

    expect(uploadFeedbackStore.getSnapshot()).toEqual([
      { key: "a:3:1", fileName: "a.png", status: "uploading", progress: 42 },
    ]);

    uploadFeedbackStore.progress("a:3:1", 240);
    expect(uploadFeedbackStore.getSnapshot()[0]?.progress).toBe(100);

    uploadFeedbackStore.succeed("a:3:1");
    uploadFeedbackStore.progress("a:3:1", 10);

    expect(uploadFeedbackStore.getSnapshot()).toEqual([
      { key: "a:3:1", fileName: "a.png", status: "ready" },
    ]);
  });

  it("returns one stable empty server snapshot for React hydration", () => {
    const first = uploadFeedbackStore.getServerSnapshot();
    const second = uploadFeedbackStore.getServerSnapshot();

    expect(first).toBe(second);
    expect(first).toEqual([]);
  });
});
