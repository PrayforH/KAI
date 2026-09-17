import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

const detail = readFileSync(
  join(process.cwd(), "src/components/knowledge/knowledge-base-detail.tsx"),
  "utf8",
);

describe("knowledge document upload", () => {
  it("lets the picker select several files at once", () => {
    expect(detail).toMatch(/<input\s+ref=\{fileRef\}\s+type="file"\s+multiple/);
  });

  it("uploads in batches of three and keeps going after a failure", () => {
    expect(detail).toContain("const UPLOAD_CONCURRENCY = 3;");
    expect(detail).toContain("await mapWithConcurrency(files, UPLOAD_CONCURRENCY, async (file) => {");
    // Each file is attempted inside the worker and its error is collected
    // rather than aborting the batch.
    const worker = detail.slice(
      detail.indexOf("await mapWithConcurrency(files, UPLOAD_CONCURRENCY"),
      detail.indexOf("const failed = results.filter"),
    );
    expect(worker).toContain("await studioClient.uploadKnowledgeDocument(reference, file)");
    expect(worker).toContain('return { name: file.name, error: cause instanceof Error ? cause.message : "上传失败" };');
  });

  it("reports batch progress and a per-file failure summary", () => {
    expect(detail).toContain("setUploadProgress({ done, total: files.length })");
    expect(detail).toContain("个文件已上传，正在解析");
    expect(detail).toContain("上传失败：${failed.map((item) => `${item.name}：${item.error}`).join(\"；\")}");
  });

  it("routes drops and picks through the same batch path", () => {
    expect(detail.match(/await uploadFiles\(files\);/g) ?? []).toHaveLength(2);
  });
});
