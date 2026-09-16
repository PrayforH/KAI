// @vitest-environment jsdom
import { beforeEach, afterEach, expect, it, vi } from "vitest";
import { readClientResource, invalidateClientReads, mutateClientResource } from "../src/lib/client-read-cache";
import { publishAuthEvent } from "../src/lib/auth-coordination";
import { studioClient } from "../src/lib/studio-client";
import { loadTaskAgentCatalog } from "../src/lib/task-agent-catalog";
beforeEach(() => invalidateClientReads());
afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals(); });
it("shares concurrent requests, reuses snapshots for 10 seconds, then revalidates", async () => {
  vi.useFakeTimers();
  const loader = vi.fn(async () => [1]);
  const a = readClientResource("list", loader);
  const b = readClientResource("list", loader);
  expect(a).toBe(b);
  await a;
  await readClientResource("list", loader);
  expect(loader).toHaveBeenCalledTimes(1);
  vi.advanceTimersByTime(10_001);
  await readClientResource("list", loader);
  expect(loader).toHaveBeenCalledTimes(2);
});
it("does not cache errors", async () => {
  const loader = vi.fn().mockRejectedValueOnce(new Error("offline")).mockResolvedValue([]);
  await expect(readClientResource("list", loader)).rejects.toThrow("offline");
  await expect(readClientResource("list", loader)).resolves.toEqual([]);
  expect(loader).toHaveBeenCalledTimes(2);
});
it("does not let an old read overwrite a post-mutation snapshot", async () => {
  let finish!: (value: string) => void;
  const old = readClientResource("list", () => new Promise<string>((resolve) => { finish = resolve; }));
  await mutateClientResource(async () => undefined);
  await readClientResource("list", async () => "new");
  finish("old"); await old;
  expect(await readClientResource("list", async () => "unexpected")).toBe("new");
});
it("clears snapshots and detaches pending reads on identity changes", async () => {
  let finish!: (value: string) => void;
  const old = readClientResource("list", () => new Promise<string>((resolve) => { finish = resolve; }));
  publishAuthEvent({ type: "signed_in", userId: "other" });
  const fresh = await readClientResource("list", async () => "other-data");
  finish("previous-user"); await old;
  expect(fresh).toBe("other-data");
  expect(await readClientResource("list", async () => "unexpected")).toBe("other-data");
});
it("deduplicates document refreshes without caching completed responses", async () => {
  const loader = vi.fn(async () => []);
  await Promise.all([readClientResource("docs", loader, 0), readClientResource("docs", loader, 0)]);
  await readClientResource("docs", loader, 0);
  expect(loader).toHaveBeenCalledTimes(2);
});
it("shares the drafts list between task catalog and Studio and refreshes after writes", async () => {
  const fetcher = vi.fn(async (url: string) => new Response(JSON.stringify(
    url.endsWith("runtime-config") ? {name:"lead", version:"1"} : [],
  )));
  vi.stubGlobal("fetch", fetcher);
  await Promise.all([loadTaskAgentCatalog("me"), studioClient.listDrafts(), studioClient.listAccessibleDrafts()]);
  expect(fetcher.mock.calls.filter(([url]) => url === "/api/studio/drafts")).toHaveLength(1);
  await studioClient.createKnowledgeBase({reference:"x",displayName:"x",description:"",sourceReferences:[]});
  await studioClient.listDrafts();
  expect(fetcher.mock.calls.filter(([url]) => url === "/api/studio/drafts")).toHaveLength(2);
});
