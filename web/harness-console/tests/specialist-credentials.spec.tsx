// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const assistantRequest = vi.fn();
const saveCredential = vi.fn();

vi.mock("../src/lib/assistant-client", () => ({
  assistantRequest: (...args: unknown[]) => assistantRequest(...args),
  saveSpecialistCredential: (...args: unknown[]) => saveCredential(...args),
}));

import { BotEditDialog } from "../src/components/bot-edit-dialog";
import { SpecialistCredentials } from "../src/components/specialist-credentials";

(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;

function specialist(config: Record<string, unknown> = {}) {
  return {
    specialistId: "specialist_cred",
    name: "行业快报监控",
    slug: "news-watch",
    description: "每天汇总行业动态",
    agentName: "archive-assistant-agent",
    agentVersion: "0.1.1",
    status: "needs_input",
    revision: 3,
    config,
    createdAt: "2026-11-01T00:00:00Z",
    updatedAt: "2026-11-01T00:00:00Z",
  };
}

const reference = "specialist_credential_abc123";
const withCredential = specialist({ "credential_ref:api_key": reference });

let root: Root | undefined;
let host: HTMLDivElement;

beforeEach(() => {
  assistantRequest.mockReset();
  saveCredential.mockReset();
  host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
});

afterEach(async () => {
  if (root) await act(async () => root!.unmount());
  root = undefined;
  host.remove();
});

async function flush(rounds = 6) {
  for (let index = 0; index < rounds; index += 1) {
    await act(async () => {
      await Promise.resolve();
    });
  }
}

function setInput(input: HTMLInputElement, value: string) {
  Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!.call(input, value);
  input.dispatchEvent(new Event("input", { bubbles: true }));
}

function button(label: string): HTMLButtonElement | null {
  return (
    [...host.querySelectorAll("button")].find((node) =>
      (node.textContent ?? "").includes(label),
    ) ?? null
  );
}

describe("specialist credentials", () => {
  it("lists configured credential names and never echoes the stored reference", () => {
    const onUpdated = vi.fn();
    act(() => {
      root!.render(
        <SpecialistCredentials
          specialistId="specialist_cred"
          specialist={withCredential as never}
          onUpdated={onUpdated}
        />,
      );
    });
    expect(host.textContent).toContain("已配置 · api_key");
    expect(host.textContent).not.toContain(reference);
    // 输入框是密码型，不显示明文。
    expect(host.querySelector<HTMLInputElement>('input[aria-label="凭据内容"]')?.type).toBe(
      "password",
    );
  });

  it("saves name/secret with the current revision, clears the inputs and lists the new name", async () => {
    const onUpdated = vi.fn();
    const updated = specialist({
      "credential_ref:api_key": "specialist_credential_new",
    });
    saveCredential.mockResolvedValue(updated);

    await act(async () => {
      root!.render(
        <SpecialistCredentials
          specialistId="specialist_cred"
          specialist={specialist() as never}
          onUpdated={onUpdated}
        />,
      );
    });
    const nameInput = host.querySelector<HTMLInputElement>('input[placeholder*="api_key"]')!;
    const secretInput = host.querySelector<HTMLInputElement>('input[aria-label="凭据内容"]')!;
    await act(async () => setInput(nameInput, "api_key"));
    await act(async () => setInput(secretInput, "sk-live-123"));
    await act(async () => button("保存凭据")!.click());
    await flush();

    expect(saveCredential).toHaveBeenCalledTimes(1);
    expect(saveCredential).toHaveBeenCalledWith("specialist_cred", {
      name: "api_key",
      secret: "sk-live-123",
      expectedRevision: 3,
    });
    expect(onUpdated).toHaveBeenCalledWith(updated);
    expect(host.textContent).toContain("已配置 · api_key");
    expect(host.textContent).toContain("已保存「api_key」");
    expect(host.textContent).not.toContain("sk-live-123");
    const clearedName = host.querySelector<HTMLInputElement>('input[placeholder*="api_key"]')!;
    const clearedSecret = host.querySelector<HTMLInputElement>('input[aria-label="凭据内容"]')!;
    expect(clearedName.value).toBe("");
    expect(clearedSecret.value).toBe("");
  });

  it("shows the server error and keeps the typed values on failure", async () => {
    saveCredential.mockRejectedValue(new Error("凭据存储未配置"));
    await act(async () => {
      root!.render(
        <SpecialistCredentials
          specialistId="specialist_cred"
          specialist={specialist() as never}
          onUpdated={vi.fn()}
        />,
      );
    });
    const nameInput = host.querySelector<HTMLInputElement>('input[placeholder*="api_key"]')!;
    const secretInput = host.querySelector<HTMLInputElement>('input[aria-label="凭据内容"]')!;
    await act(async () => setInput(nameInput, "api_key"));
    await act(async () => setInput(secretInput, "sk-live-123"));
    await act(async () => button("保存凭据")!.click());
    await flush();

    expect(host.textContent).toContain("凭据存储未配置");
    expect(host.querySelector<HTMLInputElement>('input[aria-label="凭据内容"]')!.value).toBe(
      "sk-live-123",
    );
  });
});

describe("bot edit dialog credentials section", () => {
  it("renders the credential area from the specialist detail", async () => {
    assistantRequest.mockResolvedValue({
      specialist: withCredential,
      threadId: "assistant_objective_1",
      primaryObjectiveId: "objective_1",
      duties: [],
    });
    await act(async () => {
      root!.render(
        <BotEditDialog specialistId="specialist_cred" onClose={() => {}} onSaved={() => {}} />,
      );
    });
    await flush();
    expect(host.textContent).toContain("凭据");
    expect(host.textContent).toContain("已配置 · api_key");
    expect(host.textContent).not.toContain(reference);
    expect(button("保存凭据")).not.toBeNull();
  });
});
