import { describe, expect, it } from "vitest";
import {
  isValidKnowledgeReference,
  slugifyKnowledgeReference,
} from "../src/lib/knowledge-reference";

describe("knowledge base identifier", () => {
  it("accepts the identifiers the API accepts", () => {
    for (const value of ["kb", "case-library", "kb-2026", "a1"]) {
      expect(isValidKnowledgeReference(value)).toBe(true);
    }
  });

  it("rejects what the API rejects instead of letting the form submit", () => {
    // These are the inputs that used to reach the API and come back as a raw
    // 422 validation message ("新建知识库失败").
    for (const value of ["kb_test", "KAI-KB", "知识库1", "-abc", "invalid ref", "", "  "]) {
      expect(isValidKnowledgeReference(value)).toBe(false);
    }
    expect(isValidKnowledgeReference("a".repeat(129))).toBe(false);
  });

  it("suggests an identifier from a latin name", () => {
    expect(slugifyKnowledgeReference("Case Library")).toBe("case-library");
    expect(slugifyKnowledgeReference("  非法集资  Case  Library!! ")).toBe("case-library");
    expect(slugifyKnowledgeReference("2026 Cases")).toBe("kb-2026-cases");
    expect(slugifyKnowledgeReference("Café Notes")).toBe("cafe-notes");
  });

  it("leaves the field empty when the name has no usable latin characters", () => {
    expect(slugifyKnowledgeReference("非法集资案例库")).toBe("");
    expect(slugifyKnowledgeReference("---")).toBe("");
  });
});
