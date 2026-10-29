import { afterEach, expect, it, vi } from "vitest";
const mocks = vi.hoisted(() => ({ session: vi.fn(), preview: vi.fn() }));
vi.mock("../src/lib/auth-route", () => ({ currentSession: mocks.session }));
vi.mock("../src/lib/link-preview-server", () => ({ fetchLinkPreview: mocks.preview }));
import { GET } from "../src/app/api/link-preview/route";
afterEach(() => vi.clearAllMocks());
it("requires a valid session before fetching any external page", async () => {
  mocks.session.mockResolvedValue(Response.json({ user: null }, { status: 401 }));
  expect((await GET(new Request("https://app.test/api/link-preview?url=https://example.com"))).status).toBe(401);
  expect(mocks.preview).not.toHaveBeenCalled();
});
it("preserves rotated session cookies separately and disables response caching", async () => {
  const headers = new Headers(); headers.append("Set-Cookie", "access=new; HttpOnly"); headers.append("Set-Cookie", "refresh=new; HttpOnly");
  mocks.session.mockResolvedValue(Response.json({ user: {} }, { headers })); mocks.preview.mockResolvedValue({ title: "Title", description: null });
  const result = await GET(new Request("https://app.test/api/link-preview?url=https%3A%2F%2Fexample.com%2F"));
  expect(result.headers.getSetCookie()).toHaveLength(2); expect(result.headers.get("Cache-Control")).toBe("private, no-store");
  expect(await result.json()).toEqual({ preview: { title: "Title", description: null } });
});
it("returns a quiet unavailable result for a rejected URL", async () => {
  mocks.session.mockResolvedValue(Response.json({ user: {} })); mocks.preview.mockRejectedValue(new Error("private address"));
  const result = await GET(new Request("https://app.test/api/link-preview?url=http://localhost"));
  expect(result.status).toBe(400); expect(await result.json()).toEqual({ preview: null });
});
