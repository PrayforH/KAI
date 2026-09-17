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

  it("uploads every selected file and keeps going after a failure", () => {
    expect(detail).toContain("const uploadFiles = useCallback(");
    // Each file is attempted inside the loop and its error is collected rather
    // than aborting the batch.
    const loop = detail.slice(
      detail.indexOf("for (const [index, file] of files.entries())"),
      detail.indexOf("await loadDocuments();", detail.indexOf("for (const [index, file] of files.entries())")),
    );
    expect(loop).toContain("await studioClient.uploadKnowledgeDocument(reference, file)");
    expect(loop).toContain("failed.push(");
    // One documents reload per batch, not per file.
    expect(detail.match(/await loadDocuments\(\);/g)?.length).toBeGreaterThan(0);
  });

  it("reports batch progress and a per-file failure summary", () => {
    expect(detail).toContain("setUploadProgress({ done: index + 1, total: files.length })");
    expect(detail).toContain("个文件已上传，正在解析");
    expect(detail).toContain("上传失败：${failed.join(\"；\")}");
  });

  it("routes drops and picks through the same batch path", () => {
    expect(detail.match(/await uploadFiles\(files\);/g) ?? []).toHaveLength(2);
  });
});
