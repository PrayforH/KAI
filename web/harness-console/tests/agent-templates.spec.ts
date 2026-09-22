import { expect, it, vi } from "vitest";
import {
  AGENT_TEMPLATES,
  applyAgentTemplate,
  createAgentFromTemplate,
} from "../src/lib/agent-templates";
import {
  DEFAULT_STUDIO_DRAFT,
  REQUIRED_PROMPT_HEADINGS,
} from "../src/lib/agent-studio";
import {
  studioDraftToSpec,
  type ApiAgentDraft,
  type studioClient,
} from "../src/lib/studio-client";
const original = {
  ...DEFAULT_STUDIO_DRAFT,
  id: "draft-template",
  revision: 1,
  model: "actual-configured-model",
  modelRoute: "tenant-model-route",
};
function api(draft = original): ApiAgentDraft {
  return {
    draftId: draft.id,
    revision: draft.revision,
    spec: studioDraftToSpec(draft),
    tenantId: "tenant",
    createdBy: "user",
    updatedBy: "user",
    createdAt: "2026-09-21",
    updatedAt: "2026-09-21",
    agentId: null,
    spaceId: null,
    publishedVersion: null,
    publishedHash: null,
    publishedPackageHash: null,
  };
}
it("keeps server model/runtime bindings and produces complete instructions for every starter", () => {
  for (const template of AGENT_TEMPLATES) {
    const result = applyAgentTemplate(original, template);
    expect(result.model).toBe(original.model);
    expect(result.modelRoute).toBe(original.modelRoute);
    expect(result.runtime).toBe(original.runtime);
    expect(result.taskContract?.inputs).toEqual([template.input]);
    for (const heading of REQUIRED_PROMPT_HEADINGS)
      expect(result.systemPrompt).toContain(heading);
    expect(result.evalCases[0].prompt).toBe(template.example);
  }
});
it("creates a persisted template draft with revision control", async () => {
  const createDraft = vi.fn().mockResolvedValue(api());
  const replaceDraft = vi
    .fn()
    .mockImplementation(async (draft) => api({ ...draft, revision: 2 }));
  const result = await createAgentFromTemplate(
    AGENT_TEMPLATES[0],
    "pr-reviewer-unique",
    { createDraft, replaceDraft } as unknown as typeof studioClient,
  );
  expect(result.saved).toBe(true);
  expect(result.draft.revision).toBe(2);
  expect(createDraft).toHaveBeenCalledOnce();
  expect(replaceDraft.mock.calls[0][0]).toMatchObject({
    id: original.id,
    revision: 1,
    model: original.model,
  });
});
it("retains the created draft and edited template when the second write fails", async () => {
  const createDraft = vi.fn().mockResolvedValue(api());
  const replaceDraft = vi
    .fn()
    .mockRejectedValue(new Error("Revision conflict"));
  const result = await createAgentFromTemplate(
    AGENT_TEMPLATES[1],
    "changelog-unique",
    { createDraft, replaceDraft } as unknown as typeof studioClient,
  );
  expect(result.saved).toBe(false);
  expect(result.draft.id).toBe(original.id);
  expect(result.error).toBe("Revision conflict");
  expect(result.draft.systemPrompt).toContain(AGENT_TEMPLATES[1].description);
  expect(createDraft).toHaveBeenCalledOnce();
});

it("applies a template without persisting its id as the draft domain", () => {
  // `domain` is the draft's own category and feeds its generated prompts; a
  // template id is a slug and used to be written there.
  const template = AGENT_TEMPLATES[0];
  const applied = applyAgentTemplate({ ...DEFAULT_STUDIO_DRAFT, domain: "general-assistant" }, template);
  expect(applied.domain).toBe("general-assistant");
  expect(applied.domain).not.toBe(template.id);
  expect(applied.displayName).toBe(template.name);
});
