import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

const console_ = readFileSync(
  join(process.cwd(), "src/components/knowledge/knowledge-console.tsx"),
  "utf8",
);

describe("knowledge base creation feedback", () => {
  it("rules out an identifier that is already in the loaded list", () => {
    expect(console_).toContain(
      "const referenceTaken = bases.some((base) => base.reference === reference.trim());",
    );
    expect(console_).toContain(
      "const createDisabled = creating || !referenceValid || referenceTaken || !displayName.trim();",
    );
    expect(console_).toMatch(/标识「\$\{reference\.trim\(\)\}」已经存在/);
  });

  it("explains a conflict instead of showing the raw API message", () => {
    expect(console_).toContain("cause instanceof StudioApiError && cause.status === 409");
    expect(console_).toMatch(/它可能就是你之前创建成功的那一个/);
    expect(console_).toContain("cause instanceof StudioApiError && cause.status === 422");
  });

  it("does not report a created base as a failure when the reload fails", () => {
    expect(console_).toMatch(/已创建，但列表刷新失败/);
  });
});
