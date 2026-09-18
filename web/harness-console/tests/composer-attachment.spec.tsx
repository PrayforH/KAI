import { readFileSync } from "node:fs";
import { join } from "node:path";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { FileUploadStatus } from "../src/components/composer-attachment";

const experience = readFileSync(
  join(process.cwd(), "src/app/conversation-experience.css"),
  "utf8",
);
const base = readFileSync(join(process.cwd(), "src/app/styles.css"), "utf8");
const codexTheme = readFileSync(
  join(process.cwd(), "src/app/codex-theme.css"),
  "utf8",
);

describe("composer attachment card", () => {
  it("carries the transfer state on the card instead of a row above the input", () => {
    // The standalone upload row was replaced by a per-attachment overlay, so no
    // rule may keep painting it above the composer.
    expect(base).not.toContain(".upload-feedback");
    expect(codexTheme).not.toContain(".upload-feedback");
    expect(experience).not.toContain(".upload-feedback");
    expect(experience).toContain(".composer-file-card .attachment-upload-status");
  });

  it("draws one square thumbnail per attachment", () => {
    expect(experience).toContain("flex: 0 0 72px");
    expect(experience).toContain("object-fit: cover");
    // The name moves to the tooltip and the accessibility tree, not the card.
    expect(experience).toMatch(/\.aui-attachment-text \{[^}]*clip-path: inset\(50%\)/);
    // A finished upload stays quiet apart from the ready mark.
    expect(experience).toContain(".composer-file-ready");
  });

  it("renders the transfer state the store reports", () => {
    expect(renderToStaticMarkup(
      <FileUploadStatus item={{ key: "a", fileName: "a.png", status: "uploading", progress: 42 }} />,
    )).toContain('aria-hidden="true">42%<');
    expect(renderToStaticMarkup(
      <FileUploadStatus item={{ key: "a", fileName: "a.png", status: "uploading" }} />,
    )).toContain('aria-hidden="true">上传中…<');
    const failed = renderToStaticMarkup(
      <FileUploadStatus item={{ key: "a", fileName: "a.png", status: "error", message: "too large" }} />,
    );
    // The card is one thumbnail wide: the short label is visible, the reason is not lost.
    expect(failed).toContain('aria-hidden="true">上传失败<');
    expect(failed).toContain("上传失败：too large");
    expect(failed).toContain('title="上传失败：too large"');
  });
});
