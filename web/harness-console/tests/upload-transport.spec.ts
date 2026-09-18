import { afterEach, expect, it, vi } from "vitest";
import { uploadInputFile } from "../src/lib/input-attachment-adapter";

class UploadRequest {
  static latest: UploadRequest;
  upload = { onprogress: null as null | ((event: {lengthComputable: boolean; loaded: number; total: number}) => void) };
  status = 201;
  responseText = '{"status":"ready"}';
  onload: () => void = () => {};
  onerror: () => void = () => {};
  onabort: () => void = () => {};
  open = vi.fn();
  send = vi.fn();
  getResponseHeader = () => null;
  abort = () => this.onabort();
  constructor() { UploadRequest.latest = this; }
}
afterEach(() => vi.unstubAllGlobals());
it("reports measured upload bytes and waits for the server before returning ready", async () => {
  vi.stubGlobal("XMLHttpRequest", UploadRequest);
  const progress = vi.fn(); const controller = new AbortController();
  const form = new FormData();
  const request = uploadInputFile(form, controller.signal, progress);
  const xhr = UploadRequest.latest;
  expect(xhr.open).toHaveBeenCalledWith("POST", "/api/input-artifacts");
  xhr.upload.onprogress?.({lengthComputable: true, loaded: 42, total: 100});
  expect(progress).toHaveBeenLastCalledWith(42);
  xhr.upload.onprogress?.({lengthComputable: false, loaded: 80, total: 0});
  expect(progress).toHaveBeenCalledTimes(1);
  let done = false; void request.then(() => { done = true; });
  await Promise.resolve(); expect(done).toBe(false);
  xhr.onload(); expect((await request).status).toBe(201);
});
it("cancels the in-flight transport and surfaces network failures", async () => {
  vi.stubGlobal("XMLHttpRequest", UploadRequest);
  const controller = new AbortController();
  const request = uploadInputFile(new FormData(), controller.signal, () => {});
  const rejected = expect(request).rejects.toMatchObject({ name: "AbortError" });
  controller.abort(); await rejected;
  const second = uploadInputFile(new FormData(), new AbortController().signal, () => {});
  UploadRequest.latest.onerror(); await expect(second).rejects.toThrow("网络异常");
});
