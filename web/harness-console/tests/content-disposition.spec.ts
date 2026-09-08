import { describe, expect, it } from "vitest";
import { inlineContentDisposition } from "../src/lib/content-disposition";

describe("inlineContentDisposition", () => {
  it("preserves the UTF-8 filename when turning a download into a preview", () => {
    expect(
      inlineContentDisposition(
        "attachment; filename*=UTF-8''%E6%B6%89%E9%9D%9E%E8%88%86%E6%83%85.md",
      ),
    ).toBe(
      "inline; filename*=UTF-8''%E6%B6%89%E9%9D%9E%E8%88%86%E6%83%85.md",
    );
  });

  it("falls back to a bare inline disposition when the upstream omits one", () => {
    expect(inlineContentDisposition(null)).toBe("inline");
  });
});
