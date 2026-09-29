// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, expect, it } from "vitest";
import { AssistantResponse } from "../src/components/agent-thread";

(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;
let root: Root;
let host: HTMLDivElement;

afterEach(async () => {
  if (root) await act(async () => root.unmount());
  host?.remove();
});

it("shows durable text without relying on assistant-ui part mounting", async () => {
  host = document.createElement("div");
  document.body.appendChild(host);
  root = createRoot(host);

  await act(async () => root.render(<AssistantResponse text="OK" streaming={false} />));
  expect(host.querySelector(".assistant-answer")?.textContent).toBe("OK");

  await act(async () => root.render(<AssistantResponse text="OK" streaming />));
  expect(host.querySelector(".assistant-answer")?.textContent).toBe("OK");

  await act(async () => root.render(<AssistantResponse text="OK" streaming={false} />));
  expect(host.querySelector(".assistant-answer")?.textContent).toBe("OK");
});
