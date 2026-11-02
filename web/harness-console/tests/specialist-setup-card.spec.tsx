import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

const page = readFileSync(join(process.cwd(), "src/app/page.tsx"), "utf8");

describe("pending handover entry", () => {
  it("is available in the assistant page", () => {
    expect(page).toContain("<SpecialistSetupCard");
    expect(page).toContain("onReady={() => setBotRevision((current) => current + 1)}");
  });
});
