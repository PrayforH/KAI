import { describe, expect, it } from "vitest";
import { canonicalConversationHref, taskThreadHref } from "../src/lib/conversation-navigation";
const resolve = (assistant: boolean, query: string) => canonicalConversationHref(assistant, new URLSearchParams(query));
describe("assistant and original task page ownership", () => {
  it("keeps the original task and focused run in legacy assistant result links", () => {
    expect(resolve(true, "thread=original&run=run-result&computer=open&trace=1"))
      .toBe("/?thread=original&run=run-result&trace=1");
    expect(resolve(false, "thread=original&run=run-result")).toBeNull();
  });
  it("gives an explicit specialist priority over a stale task parameter", () => {
    expect(resolve(true, "bot=specialist-a&thread=stale&run=run-bot"))
      .toBe("/assistant?bot=specialist-a&run=run-bot");
    expect(resolve(true, "bot=specialist-a")).toBeNull();
  });
  it("resumes the same personal thread from old task URLs without a duplicate conversation", () => {
    expect(resolve(false, "thread=kai-personal-assistant-v1&run=run-personal"))
      .toBe("/assistant?run=run-personal");
    expect(resolve(true, "thread=kai-personal-assistant-v1")).toBe("/assistant");
    expect(resolve(true, "")).toBeNull();
  });
  it("discards task launch coordinates on the assistant entry", () => {
    expect(resolve(true, "project=old&tab=files&agent=builder&version=1&owner=me&skill=skill-creator&run=owned"))
      .toBe("/assistant?run=owned");
    expect(resolve(true, "bot=specialist-a&project=old&skillScope=agent&computer=open"))
      .toBe("/assistant?bot=specialist-a&computer=open");
    expect(resolve(false, "project=kept&skill=skill-creator")).toBeNull();
  });
  it("encodes task coordinates without inheriting bot state or adding query instructions", () => {
    const url = new URL(taskThreadHref("原任务&bot=other#fragment"), "https://kai.test");
    expect(url.pathname).toBe("/");
    expect([...url.searchParams]).toEqual([["thread", "原任务&bot=other#fragment"]]);
    expect(url.hash).toBe("");
  });
});
