import { emitHandover } from "./helpers/handover";
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

import { HandoverInlineRows } from "../src/components/handover-row";

(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;

function specialist(config: Record<string, unknown>, revision = 3) {
  return {
    specialistId: "specialist_cred_gate",
    name: "行业快报监控",
    slug: "news-watch",
    description: "每天汇总动态",
    agentName: "archive-assistant-agent",
    agentVersion: "0.1.1",
    status: "active",
    revision,
    config,
    createdAt: "2026-11-01T00:00:00Z",
    updatedAt: "2026-11-01T00:00:00Z",
  };
}

function detailFor(value: ReturnType<typeof specialist>, extra: Record<string, unknown> = {}) {
  return {
    specialist: value,
    threadId: "assistant_objective_1",
    primaryObjectiveId: "objective_1",
    duties: [
      {
        objectiveId: "objective_1",
        title: "每天检查",
        status: "active",
        schedule: { everyMinutes: 1440 },
      },
    ],
    ...extra,
  };
}

let root: Root | undefined;
let host: HTMLDivElement;
let details: Map<string, ReturnType<typeof detailFor>>;

beforeEach(() => {
  assistantRequest.mockReset();
  saveCredential.mockReset();
  details = new Map();
  assistantRequest.mockImplementation(async (path: string) => {
    if (path === "handovers") return [];
    if (path === "specialists") return [];
    const match = path.match(/^specialists\/(.+)$/);
    if (match) {
      const detail = details.get(decodeURIComponent(match[1]));
      if (detail) return detail;
      throw new Error(`unknown specialist ${match[1]}`);
    }
    throw new Error(`unexpected request ${path}`);
  });
  host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
});

afterEach(async () => {
  if (root) await act(async () => root!.unmount());
  root = undefined;
  host.remove();
});

async function flush(rounds = 8) {
  for (let index = 0; index < rounds; index += 1) {
    await act(async () => {
      await Promise.resolve();
    });
  }
}

async function mountWithHandover(
  specialistId: string,
  status: string,
  name: string,
) {
  await act(async () => {
    root!.render(<HandoverInlineRows messageId="assistant-run-ui-test" />);
  });
  await flush(2);
  await act(async () => {
    emitHandover({ specialist_id: specialistId, specialist_name: name, status });
  });
  await flush();
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

describe("required credentials from config.required_credentials", () => {
  it("shows the credential card inline and saves the declared name", async () => {
    const pending = specialist({
      required_credentials: ["vendor_api"],
      schedule_minutes: 1440,
      notify_channel: "inbox",
    });
    details.set("specialist_cred_gate", detailFor(pending));
    const updated = specialist({
      required_credentials: ["vendor_api"],
      "credential_ref:vendor_api": "specialist_credential_new",
      schedule_minutes: 1440,
      notify_channel: "inbox",
    }, 4);
    saveCredential.mockResolvedValue(updated);

    await mountWithHandover("specialist_cred_gate", "applied", "行业快报监控");

    // 非 pending_input 行：没有 SetupCard，但必填凭据卡出现。
    expect(host.querySelector(".specialist-setup-card")).toBeNull();
    const credentialCard = host.querySelector(".handover-credential-card");
    expect(credentialCard).not.toBeNull();
    expect(credentialCard?.textContent).toContain("必填凭据");
    expect(credentialCard?.textContent).toContain("待补凭据：vendor_api");
    const select = credentialCard?.querySelector<HTMLSelectElement>('select[aria-label="凭据名称"]');
    expect(select?.value).toBe("vendor_api");
    const secret = credentialCard?.querySelector<HTMLInputElement>('input[aria-label="凭据内容"]')!;
    expect(secret.type).toBe("password");

    await act(async () => setInput(secret, "sk-gate-test"));
    await act(async () => button("保存凭据")!.click());
    await flush();

    expect(saveCredential).toHaveBeenCalledWith("specialist_cred_gate", {
      name: "vendor_api",
      secret: "sk-gate-test",
      expectedRevision: 3,
    });
    // 已满足 → 卡收起，行态仍是已安排；没有跳转 ?bot=。
    expect(host.querySelector(".handover-credential-card")).toBeNull();
    expect(host.textContent ?? "").not.toContain("待补凭据");
    expect(host.textContent ?? "").not.toContain("sk-gate-test");
    expect(host.textContent ?? "").toContain("已发送消息给");
    expect(window.location.pathname + window.location.search).toBe("/");
  });

  it("stays quiet when every declared credential is already configured", async () => {
    const ready = specialist({
      required_credentials: ["vendor_api"],
      "credential_ref:vendor_api": "specialist_credential_existing",
      schedule_minutes: 1440,
      notify_channel: "inbox",
    });
    details.set("specialist_cred_gate", detailFor(ready));

    await mountWithHandover("specialist_cred_gate", "applied", "行业快报监控");

    expect(host.querySelector(".handover-credential-card")).toBeNull();
    expect(host.textContent ?? "").not.toContain("待补凭据");
    expect(host.textContent ?? "").not.toContain("specialist_credential_existing");
  });

  it("stacks the credential card with the pending_input setup card", async () => {
    const pending = specialist(
      { required_credentials: ["vendor_api"] },
      3,
    );
    details.set("specialist_cred_gate", detailFor({ ...pending, status: "needs_input" }));

    await mountWithHandover("specialist_cred_gate", "pending_input", "行业快报监控");

    expect(host.querySelector(".specialist-setup-card")).not.toBeNull();
    const credentialCard = host.querySelector(".handover-credential-card");
    expect(credentialCard).not.toBeNull();
    expect(credentialCard?.textContent).toContain("待补凭据：vendor_api");
  });
});
