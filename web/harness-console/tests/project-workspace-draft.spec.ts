import { expect, it } from "vitest";
import { projectDraft, draftProject } from "../src/lib/project-workspace";
import { createUserScopedStorage, isLocalThread, markThreadPersisted } from "../src/lib/thread-store";
import { projectHref } from "../src/lib/project-space";

it("preserves distinct unsent drafts per project and user, and rotates a draft after execution", () => {
  const values = new Map<string, string>();
  const raw = { getItem: (key: string) => values.get(key) ?? null, setItem: (key: string, value: string) => { values.set(key, value); }, removeItem: (key: string) => { values.delete(key); } };
  const alice = createUserScopedStorage(raw, "alice");
  const bob = createUserScopedStorage(raw, "bob");
  const a = projectDraft(alice, "project-a");
  const b = projectDraft(alice, "project-b");
  expect(a).not.toBe(b);
  expect(projectDraft(alice, "project-a")).toBe(a);
  expect(projectDraft(bob, "project-a")).not.toBe(a);
  expect(draftProject(alice, a)).toBe("project-a");
  expect(isLocalThread(alice, a)).toBe(true);
  markThreadPersisted(alice, a);
  expect(draftProject(alice, a)).toBeNull();
  expect(projectDraft(alice, "project-a")).not.toBe(a);
  expect(projectDraft(alice, "project-b")).toBe(b);
});

it("routes project and result links into Home with encoded project coordinates", () => {
  expect(projectHref("project/a")).toBe("/?project=project%2Fa");
  expect(projectHref("project/a", "results")).toBe("/?project=project%2Fa&tab=results");
});
