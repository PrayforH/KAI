import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

type ModelApiFormat =
  | "anthropic_compatible"
  | "openai_compatible"
  | "openai_images"
  | "openai_videos";

type RuntimeCapabilityFixture = {
  runtime: "claude-agent-sdk" | "codex-app-server" | "deepagents";
  label: string;
  stability: "stable" | "preview" | "experimental";
  capabilities: string[];
  modelApiFormats: ModelApiFormat[];
  limitations: string[];
};

const fixturePath = join(
  process.cwd(),
  "..",
  "..",
  "tests",
  "fixtures",
  "runtime",
  "runtime_capabilities_v0.json",
);
const fixture = JSON.parse(
  readFileSync(fixturePath, "utf8"),
) as RuntimeCapabilityFixture[];

describe("RuntimeCapabilities v0 contract (shared fixture with the compiler)", () => {
  it("covers exactly the runtimes the Builder can render", () => {
    expect(fixture.map((item) => item.runtime).sort()).toEqual([
      "claude-agent-sdk",
      "codex-app-server",
      "deepagents",
    ]);
  });

  it("carries the display fields the Builder consumes", () => {
    for (const item of fixture) {
      expect(item.label.length).toBeGreaterThan(0);
      expect(["stable", "preview", "experimental"]).toContain(item.stability);
      expect(Array.isArray(item.capabilities)).toBe(true);
      expect(item.modelApiFormats.length).toBeGreaterThan(0);
      expect(Array.isArray(item.limitations)).toBe(true);
    }
  });

  it("matches the compiler protocol conclusions for model routes", () => {
    const byRuntime = new Map(fixture.map((item) => [item.runtime, item]));
    const claude = byRuntime.get("claude-agent-sdk");
    const codex = byRuntime.get("codex-app-server");
    expect(claude?.modelApiFormats).toContain("anthropic_compatible");
    expect(codex?.modelApiFormats).not.toContain("anthropic_compatible");
    expect(codex?.modelApiFormats).toContain("openai_compatible");
  });

  it("keeps codex limitations server-driven so the Builder never hardcodes copy", () => {
    const codex = fixture.find((item) => item.runtime === "codex-app-server");
    expect(codex?.limitations.length ?? 0).toBeGreaterThan(0);
  });

  it("declares DeepAgents as a text-only preview with its limits stated", () => {
    const deepagents = fixture.find((item) => item.runtime === "deepagents");
    expect(deepagents?.stability).toBe("preview");
    expect(deepagents?.modelApiFormats).toEqual([
      "anthropic_compatible",
      "openai_compatible",
    ]);
    // Sub Agents and knowledge are compile-time errors for this runtime, so the
    // capability list must not promise them.
    expect(deepagents?.capabilities).not.toContain("subagents");
    expect(deepagents?.capabilities).not.toContain("knowledge");
    expect(deepagents?.limitations.length ?? 0).toBeGreaterThan(0);
  });
});
