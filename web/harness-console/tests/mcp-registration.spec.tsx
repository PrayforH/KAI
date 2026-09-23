// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { McpCatalogControlPlane } from "../src/components/agent-studio/mcp-catalog-control-plane";
import { studioClient } from "../src/lib/studio-client";

vi.mock("../src/components/auth-provider", () => ({ useAuth: () => ({ membership: { role: "owner" } }) }));
vi.mock("../src/lib/studio-client", () => ({ studioClient: { catalog: vi.fn(), listMcpCredentials: vi.fn(), discoverMcp: vi.fn(), upsertMcp: vi.fn() } }));
let root: Root; let host: HTMLDivElement;
const record = { revision: 3, catalog: { mcpServers: [], executionProfiles: [{ profileId: "local", enabled: true, sandboxProvider: "local", networkAccess: ["external"] }] } };
const close = vi.fn(); const registered = vi.fn();
beforeEach(async () => {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
  vi.resetAllMocks();
  vi.mocked(studioClient.catalog).mockResolvedValue(record as never);
  vi.mocked(studioClient.listMcpCredentials).mockResolvedValue([]);
  host = document.createElement("div"); document.body.append(host); root = createRoot(host);
  await act(async () => root.render(<McpCatalogControlPlane startInForm onClose={close} onRegistered={registered} />));
});
afterEach(async () => { await act(async () => root.unmount()); host.remove(); });
const dialog = () => document.querySelector('[role="dialog"]')!;
const button = (label: string) => [...dialog().querySelectorAll("button")].find(item => item.textContent === label)!;
async function fill(placeholder: string, value: string) {
  const input = dialog().querySelector<HTMLInputElement>(`input[placeholder="${placeholder}"]`)!;
  await act(async () => { Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!.call(input, value); input.dispatchEvent(new Event("input", { bubbles: true })); });
}

it("keeps the catalog outside the embedded surface and reports connection errors inside the form", async () => {
  expect(host.textContent).toBe("");
  expect(document.querySelector('input[aria-label="搜索能力"]')).toBeNull();
  const advanced = dialog().querySelector("details")!;
  expect(advanced.open).toBe(false);
  for (const text of ["引用标识", "能力说明", "风险级别", "只读能力"]) expect(advanced.textContent).toContain(text);
  expect(button("完成注册").disabled).toBe(true);
  await fill("企业搜索", "企业搜索"); await fill("https://mcp.example.com/mcp", "https://mcp.example.com/mcp");
  vi.mocked(studioClient.discoverMcp).mockRejectedValue(new Error("连接失败，请检查地址"));
  await act(async () => button("检测地址").click());
  expect(dialog().querySelector('[role="alert"]')?.textContent).toContain("连接失败，请检查地址");
  expect(studioClient.discoverMcp).toHaveBeenCalledWith(expect.objectContaining({ reference: expect.stringMatching(/^mcp-[a-z0-9]{8}$/), endpointUrl: "https://mcp.example.com/mcp" }));
});

it("registers with generated metadata, preserves governance defaults and returns the new resource", async () => {
  await fill("企业搜索", "企业搜索"); await fill("https://mcp.example.com/mcp", "https://mcp.example.com/mcp");
  vi.mocked(studioClient.discoverMcp).mockResolvedValue({ endpointUrl: "https://mcp.example.com/mcp", transport: "http", tools: [{ canonicalName: "mcp__search__query", name: "query", description: "查询" }], latencyMs: 12 } as never);
  await act(async () => button("检测地址").click());
  vi.mocked(studioClient.upsertMcp).mockResolvedValue({ record, impact: { draftIds: [] } } as never);
  await act(async () => dialog().querySelector("form")!.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true })));
  expect(studioClient.upsertMcp).toHaveBeenCalledWith(expect.any(String), 3, expect.objectContaining({ label: "企业搜索", description: "企业搜索", readOnly: false, sendsUserData: true, preflightRequired: true, tools: ["mcp__search__query"] }), ["local"]);
  expect(registered).toHaveBeenCalledWith(expect.stringMatching(/^mcp-/));
  expect(close).toHaveBeenCalledOnce();
});
