import { afterEach, expect, it, vi } from "vitest";
import { PATCH } from "../src/app/api/studio/[...path]/route";

afterEach(() => {
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
});

it("forwards authenticated project rename through the Studio PATCH route", async () => {
  vi.stubEnv("HARNESS_API_URL", "http://harness.internal:8000");
  const upstream = vi.fn<typeof fetch>(async () => Response.json({ projectId: "project-1", name: "Renamed" }));
  vi.stubGlobal("fetch", upstream);
  const response = await PATCH(new Request("http://console.test/api/studio/projects/project-1", {
    method: "PATCH",
    headers: { "Content-Type": "application/json", Cookie: "harness_access_token=user-jwt" },
    body: JSON.stringify({ name: "Renamed" }),
  }), { params: Promise.resolve({ path: ["projects", "project-1"] }) });

  expect(response.status).toBe(200);
  expect(await response.json()).toEqual({ projectId: "project-1", name: "Renamed" });
  expect(upstream).toHaveBeenCalledWith("http://harness.internal:8000/v1/studio/projects/project-1",
    expect.objectContaining({ method: "PATCH" }));
  const options = upstream.mock.calls[0]?.[1] as RequestInit | undefined;
  expect(await new Response(options?.body).json()).toEqual({ name: "Renamed" });
  expect(new Headers(options?.headers).get("Authorization")).toBe("Bearer user-jwt");
});
