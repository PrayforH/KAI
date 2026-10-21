import { describe, expect, it, vi, afterEach } from "vitest";
import { EventEmitter } from "node:events";
const network = vi.hoisted(() => ({ lookup: vi.fn(), request: vi.fn() }));
vi.mock("node:dns/promises", () => ({ lookup: network.lookup }));
vi.mock("node:http", () => ({ request: network.request }));
vi.mock("node:https", () => ({ request: network.request }));
import { fetchLinkPreview, isPublicPreviewAddress, parsePreview, previewURL } from "../src/lib/link-preview-server";
afterEach(() => vi.clearAllMocks());
describe("public URL policy", () => {
  it.each(["http://localhost/a", "https://x.local/a", "http://127.1", "http://2130706433", "http://[::1]", "http://[::ffff:8.8.8.8]", "http://[64:ff9b::808:808]", "http://10.0.0.1", "http://user:pass@example.com", "https://example.com:3501", "file:///tmp/x", "https://example.com/\n"])('rejects %s', url => expect(() => previewURL(url)).toThrow());
  it.each(["0.0.0.0", "100.64.0.1", "169.254.169.254", "172.20.109.174", "192.168.1.1", "198.18.0.1", "224.0.0.1", "2001:db8::1", "2002:808:808::1", "fe80::1", "::ffff:127.0.0.1"])('blocks %s', ip => expect(isPublicPreviewAddress(ip)).toBe(false));
  it("accepts global addresses and strips fragments only for fetching", () => {
    expect(isPublicPreviewAddress("8.8.8.8")).toBe(true);
    expect(isPublicPreviewAddress("2606:4700:4700::1111")).toBe(true);
    expect(previewURL("https://example.com/a?q=1#part").href).toBe("https://example.com/a?q=1");
  });
});
it("extracts OG metadata, entities and title fallback without evaluating HTML", () => {
  expect(parsePreview(`<head><title>Fallback</title><!-- <meta property="og:title" content="wrong"> --><script>"<meta property='og:title' content='wrong'>"</script><meta content='Repo &amp; docs &#x1F331;' property='og:title'><meta name="description" content="A &quot;short&quot; summary"></head>`)).toEqual({ title: "Repo & docs 🌱", description: 'A "short" summary' });
  expect(parsePreview('<title>标题 &middot; example</title><body>secret')).toEqual({ title: "标题 · example", description: null });
});
it("bounds metadata lengths and safely handles invalid code points", () => {
  expect(parsePreview(`<meta property="og:title" content="${"x".repeat(1000)}"><meta name="description" content="&#99999999; &#xD800; fine">`)).toEqual({ title: "x".repeat(300), description: "fine" });
});
function reply(status: number, headers: Record<string, string>, html = "") {
  network.request.mockImplementationOnce((_url, _options, callback) => {
    const req = new EventEmitter() as EventEmitter & { end: () => void };
    req.end = () => {
      const response = Object.assign(new EventEmitter(), { statusCode: status, headers, destroy: vi.fn() });
      callback(response); response.emit("data", Buffer.from(html)); response.emit("end");
    };
    return req;
  });
}
it("rejects mixed public/private DNS answers before opening a socket", async () => {
  network.lookup.mockResolvedValue([{ address: "8.8.8.8", family: 4 }, { address: "127.0.0.1", family: 4 }]);
  expect(await fetchLinkPreview("https://mixed.example/test")).toBeNull(); expect(network.request).not.toHaveBeenCalled();
});
it("pins the socket, never forwards credentials, bounds redirects and shares cached fetches", async () => {
  network.lookup.mockResolvedValue([{ address: "8.8.8.8", family: 4 }]);
  reply(200, { "content-type": "text/html" }, '<title>Public title</title></head>');
  const [a, b] = await Promise.all([fetchLinkPreview("https://public.example/test#a"), fetchLinkPreview("https://public.example/test#b")]);
  expect(a).toEqual({ title: "Public title", description: null }); expect(b).toEqual(a);
  expect(network.request).toHaveBeenCalledTimes(1);
  expect(network.request.mock.calls[0][1]).toMatchObject({ hostname: "8.8.8.8", servername: "public.example", agent: false, headers: { Host: "public.example" } });
  expect(network.request.mock.calls[0][1].headers).not.toHaveProperty("Authorization");
});
it("validates every redirect and refuses private destinations", async () => {
  network.lookup.mockResolvedValue([{ address: "8.8.8.8", family: 4 }]); reply(302, { location: "http://169.254.169.254/latest" });
  expect(await fetchLinkPreview("https://redirect.example/test")).toBeNull(); expect(network.request).toHaveBeenCalledTimes(1);
});
it("rejects non-HTML responses", async () => {
  network.lookup.mockResolvedValue([{ address: "8.8.8.8", family: 4 }]); reply(200, { "content-type": "application/pdf" });
  expect(await fetchLinkPreview("https://binary.example/test")).toBeNull();
});
it("rechecks DNS after public redirects and blocks a private answer", async () => {
  network.lookup.mockResolvedValueOnce([{ address: "8.8.8.8", family: 4 }]).mockResolvedValueOnce([{ address: "10.0.0.1", family: 4 }]);
  reply(302, { location: "https://second.example/path" });
  expect(await fetchLinkPreview("https://first.example/path")).toBeNull(); expect(network.request).toHaveBeenCalledTimes(1);
});
it("limits redirect loops and page bytes", async () => {
  network.lookup.mockResolvedValue([{ address: "8.8.8.8", family: 4 }]);
  for (let i = 0; i < 4; i++) reply(302, { location: `/loop/${i}` });
  expect(await fetchLinkPreview("https://loop.example/start")).toBeNull(); expect(network.request).toHaveBeenCalledTimes(4);
  reply(200, { "content-type": "text/html" }, ' '.repeat(512 * 1024) + '<title>Beyond budget</title>');
  expect(await fetchLinkPreview("https://large.example/start")).toBeNull();
});
