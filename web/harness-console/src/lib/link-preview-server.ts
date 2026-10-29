import { lookup } from "node:dns/promises";
import type { LookupAddress } from "node:dns";
import { request as httpRequest, type RequestOptions } from "node:http";
import { request as httpsRequest } from "node:https";
import { BlockList, isIP, type Socket } from "node:net";
import { connect as tlsConnect } from "node:tls";
import type { LinkPreview } from "./link-preview";

const denied = new BlockList();
for (const [ip, bits] of [["0.0.0.0", 8], ["10.0.0.0", 8], ["100.64.0.0", 10], ["127.0.0.0", 8], ["169.254.0.0", 16], ["172.16.0.0", 12], ["192.0.0.0", 24], ["192.0.2.0", 24], ["192.88.99.0", 24], ["192.168.0.0", 16], ["198.18.0.0", 15], ["198.51.100.0", 24], ["203.0.113.0", 24], ["224.0.0.0", 3]] as const) denied.addSubnet(ip, bits);
const globalV6 = new BlockList();
globalV6.addSubnet("2000::", 3, "ipv6");
for (const [ip, bits] of [["2001::", 23], ["2001:db8::", 32], ["2002::", 16], ["3fff::", 20]] as const) denied.addSubnet(ip, bits, "ipv6");
export function isPublicPreviewAddress(ip: string): boolean {
  const family = isIP(ip);
  return family === 4 ? !denied.check(ip, "ipv4") : family === 6 && globalV6.check(ip, "ipv6") && !denied.check(ip, "ipv6");
}
export function previewURL(value: string): URL {
  if (value.length > 2048 || /[\s\\\u0000-\u001f\u007f]/.test(value)) throw new Error("Invalid URL");
  const url = new URL(value);
  const host = url.hostname.replace(/^\[|\]$/g, "");
  if (!["http:", "https:"].includes(url.protocol) || url.username || url.password || (url.port && !["80", "443"].includes(url.port)) || /(^|\.)(localhost|local|internal)$/.test(host) || (isIP(host) && !isPublicPreviewAddress(host))) throw new Error("Unsupported URL");
  url.hash = "";
  return url;
}
const named: Record<string, string> = { amp: "&", quot: '"', apos: "'", lt: "<", gt: ">", nbsp: " ", ndash: "–", mdash: "—", hellip: "…", middot: "·", copy: "©", reg: "®", trade: "™", rsquo: "’", lsquo: "‘", rdquo: "”", ldquo: "“" };
function plain(value: string, limit: number): string | null {
  return value.replace(/&(#x[\da-f]+|#\d+|[a-z]+);/gi, (original, entity: string) => {
    if (entity[0] !== "#") return named[entity] ?? original;
    const n = entity[1].toLowerCase() === "x" ? parseInt(entity.slice(2), 16) : Number(entity.slice(1));
    return n > 0 && n <= 0x10ffff && !(n >= 0xd800 && n <= 0xdfff) ? String.fromCodePoint(n) : "";
  }).replace(/[\u0000-\u001f\u007f]/g, " ").replace(/\s+/g, " ").trim().slice(0, limit) || null;
}
/** Only extract bounded text. Never render remote HTML, scripts, icons or images. */
export function parsePreview(html: string): LinkPreview {
  const head = html.split(/<\/head\s*>/i)[0].replace(/<!--[\s\S]*?-->|<(script|style)\b[^>]*>[\s\S]*?<\/\1\s*>/gi, "");
  const metadata = new Map<string, string>();
  for (const match of head.matchAll(/<meta\b(?:[^>"']|"[^"]*"|'[^']*')*>/gi)) {
    const attrs = new Map<string, string>();
    for (const attr of match[0].matchAll(/([\w:-]+)\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s>]+))/g)) attrs.set(attr[1].toLowerCase(), attr[2] ?? attr[3] ?? attr[4]);
    const key = (attrs.get("property") ?? attrs.get("name"))?.toLowerCase();
    if (key && attrs.has("content") && !metadata.has(key)) metadata.set(key, attrs.get("content")!);
  }
  return {
    title: plain(metadata.get("og:title") ?? metadata.get("twitter:title") ?? head.match(/<title\b[^>]*>([\s\S]*?)<\/title\s*>/i)?.[1] ?? "", 300),
    description: plain(metadata.get("og:description") ?? metadata.get("description") ?? metadata.get("twitter:description") ?? "", 600),
  };
}
const MAX_BYTES = 512 * 1024;
const cache = new Map<string, { expires: number; value: Promise<LinkPreview | null> }>();
let active = 0;
/** CONNECT to the validated IP, never let the proxy resolve the target again. */
export async function previewProxySocket(proxyValue: string, address: string, url: URL, signal: AbortSignal): Promise<Socket> {
  const proxy = new URL(proxyValue);
  if (proxy.protocol !== "http:" || proxy.username || proxy.password) throw new Error("Unsupported preview proxy");
  const authority = `${isIP(address) === 6 ? `[${address}]` : address}:${url.port || (url.protocol === "https:" ? 443 : 80)}`;
  return new Promise((resolve, reject) => {
    const req = httpRequest({ hostname: proxy.hostname, port: proxy.port || 80, method: "CONNECT", path: authority, headers: { Host: authority }, signal, agent: false });
    req.on("connect", (response, socket, head) => {
      if (response.statusCode !== 200) { socket.destroy(); reject(new Error("Proxy refused tunnel")); return; }
      if (head.length) socket.unshift(head);
      const abort = () => socket.destroy();
      signal.addEventListener("abort", abort, { once: true });
      socket.once("close", () => signal.removeEventListener("abort", abort));
      socket.on("error", reject);
      if (signal.aborted) { socket.destroy(); reject(signal.reason); return; }
      resolve(url.protocol === "https:" ? tlsConnect({ socket, servername: url.hostname.replace(/^\[|\]$/g, ""), ALPNProtocols: ["http/1.1"] }) : socket);
    });
    req.on("error", reject); req.end();
  });
}
async function page(url: URL, signal: AbortSignal): Promise<{ html?: string; redirect?: string }> {
  const host = url.hostname.replace(/^\[|\]$/g, "");
  signal.throwIfAborted();
  const answers = await new Promise<LookupAddress[]>((resolve, reject) => {
    const abort = () => reject(signal.reason);
    signal.addEventListener("abort", abort, { once: true });
    void lookup(host, { all: true, verbatim: true }).then(resolve, reject).finally(() => signal.removeEventListener("abort", abort));
  });
  signal.throwIfAborted();
  if (!answers.length || answers.some(answer => !isPublicPreviewAddress(answer.address))) throw new Error("Non-public address");
  // Pin the socket to a checked address; keep the URL's Host and TLS SNI intact.
  const address = answers[0];
  const proxy = process.env.HARNESS_WEB_TOOLS_PROXY?.trim();
  const socket = proxy ? await previewProxySocket(proxy, address.address, url, signal) : null;
  signal.throwIfAborted();
  return new Promise((resolve, reject) => {
    const options: RequestOptions & { servername: string } = { signal, ...(socket ? { createConnection: () => socket } : { agent: false }), hostname: address.address, servername: host, headers: { Host: url.host, Accept: "text/html,application/xhtml+xml", "Accept-Encoding": "identity", "User-Agent": "AgentStudio-LinkPreview/1.0" } };
    const req = (url.protocol === "https:" ? httpsRequest : httpRequest)(url, options, response => {
      if ([301, 302, 303, 307, 308].includes(response.statusCode ?? 0) && response.headers.location) {
        resolve({ redirect: response.headers.location }); response.destroy(); return;
      }
      if (response.statusCode !== 200 || !/^application\/xhtml\+xml\b|^text\/html\b/i.test(response.headers["content-type"] ?? "") || (response.headers["content-encoding"] && response.headers["content-encoding"] !== "identity")) {
        response.destroy(); reject(new Error("Not an HTML page")); return;
      }
      let bytes = 0;
      const chunks: Buffer[] = [];
      response.on("error", reject);
      response.on("data", (chunk: Buffer) => {
        const part = chunk.subarray(0, MAX_BYTES - bytes); chunks.push(part); bytes += part.length;
        // Stop at the head or byte budget instead of downloading the whole site.
        if (bytes >= MAX_BYTES || Buffer.concat(chunks).includes(Buffer.from("</head>"))) {
          resolve({ html: Buffer.concat(chunks).toString("utf8") }); response.destroy();
        }
      });
      response.on("end", () => resolve({ html: Buffer.concat(chunks).toString("utf8") }));
    });
    req.on("error", reject); req.end();
  });
}
export async function fetchLinkPreview(value: string): Promise<LinkPreview | null> {
  let url = previewURL(value);
  const key = url.href, now = Date.now();
  const existing = cache.get(key);
  if (existing && existing.expires > now) return existing.value;
  if (active >= 8) return null;
  const promise = (async () => {
    active++;
    try {
      const signal = AbortSignal.timeout(5000);
      for (let redirects = 0; redirects <= 3; redirects++) {
        const result = await page(url, signal);
        if (result.redirect) { url = previewURL(new URL(result.redirect, url).href); continue; }
        const data = parsePreview(result.html ?? "");
        return data.title || data.description ? data : null;
      }
      return null;
    } catch { return null; }
    finally { active--; }
  })();
  if (cache.size >= 256) cache.delete(cache.keys().next().value!);
  cache.set(key, { expires: now + 30_000, value: promise });
  return promise;
}
