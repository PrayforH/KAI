import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
const page = readFileSync(join(process.cwd(), "src/app/page.tsx"), "utf8");
const handover = readFileSync(join(process.cwd(), "src/components/handover-row.tsx"), "utf8");
describe("one-off handover confirmation placement", () => {
  it("does not display a standing start card above a Bot conversation", () => {
    expect(page).not.toContain("<SpecialistSetupCard");
  });
  it("retains genuine pending-input confirmation on the corresponding handover", () => {
    expect(handover).toContain("<SpecialistSetupCard");
    expect(handover).toContain("objectiveId=");
  });
});
