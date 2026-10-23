import type { RunActivity } from "./activity-schema";
import type { ConversationImage } from "../components/conversation-image-viewer";

export function publicImageUrl(value: unknown): string | undefined {
  if (typeof value !== "string" || value.length > 2048) return;
  try {
    const url = new URL(value);
    const host = url.hostname.replace(/^\[|\]$/g, "").toLowerCase();
    if (url.protocol !== "https:" || url.username || url.password || (url.port && url.port !== "443")) return;
    if (!host || host === "localhost" || /\.(localhost|local|internal)$/.test(host) || host.includes(":")) return;
    // Result images load directly in the browser, never through a server proxy.
    if (/^\d+\.\d+\.\d+\.\d+$/.test(host)) {
      const [a, b] = host.split(".").map(Number);
      if (a === 0 || a === 10 || a === 127 || a >= 224 || (a === 169 && b === 254)
        || (a === 172 && b >= 16 && b <= 31) || (a === 192 && b === 168) || (a === 100 && b >= 64 && b <= 127)) return;
    }
    url.hash = "";
    return url.href;
  } catch { return; }
}

export const requestsWebImages = (text: string) => /查图|找图|搜图|图片|照片|配图|图集|相册|\b(images?|photos?|pictures?|gallery)\b/i.test(text);
const webTool = (name: string) => /^(WebSearch|WebFetch|image_query|image_search|search_images)$/i.test(name)
  || (/web|image/i.test(name) && /search|fetch|query/i.test(name));

export function imageReferences(value: unknown, depth = 0): unknown[] {
  if (depth > 6) return [];
  if (typeof value === "string") {
    if (value.length > 200_000) return [];
    try { return imageReferences(JSON.parse(value), depth + 1); } catch { return []; }
  }
  if (Array.isArray(value)) return value.slice(0, 16).flatMap(item => imageReferences(item, depth + 1));
  if (!value || typeof value !== "object") return [];
  const item = value as Record<string, unknown>;
  if (item.is_error === true || item.redacted === true) return [];
  if (Array.isArray(item.images)) return item.images.slice(0, 12);
  return imageReferences(item.content ?? item.text, depth + 1);
}

/** Turn-owned image search results; the activity metadata also survives reloads. */
export function replyWebImages(parts: readonly unknown[], activity: RunActivity | undefined, answer: string, userPrompt = ""): ConversationImage[] {
  const seen = new Set([...answer.matchAll(/!\[[^\]]*\]\(\s*<?([^\s)>]+)/g)]
    .flatMap(match => publicImageUrl(match[1]) ?? []));
  const images: ConversationImage[] = [];
  function add(values: unknown[]) {
    for (const value of values) {
      if (images.length >= 12) break;
      const record = typeof value === "string" ? { url: value } : value as Record<string, unknown> | null;
      if (!record || typeof record !== "object") continue;
      const src = publicImageUrl(record.url ?? record.content_url ?? record.image_url);
      if (!src || seen.has(src)) continue;
      seen.add(src);
      const title = String(record.description || record.title || "网页图片").slice(0, 300);
      images.push({ src, title, sourceUrl: publicImageUrl(record.source_url) });
    }
  }
  const requests = new Map<string, { name: string; args: Record<string, unknown> }>();
  const eligible = (name: string, args: Record<string, unknown>) => webTool(name) && (
    requestsWebImages(userPrompt) || /image/i.test(name) || args.include_images === true || requestsWebImages(String(args.query ?? ""))
  );
  for (const part of parts) {
    const item = part as Record<string, unknown> | null;
    if (!item || item.type !== "tool-call" || item.isError === true) continue;
    const name = String(item.toolName ?? "");
    const args = (item.args ?? {}) as Record<string, unknown>;
    if (eligible(name, args)) add(imageReferences(item.result));
  }
  for (const item of activity?.items ?? []) {
    const id = String(item.metadata.tool_call_id ?? "");
    if (item.event_type === "tool.request") requests.set(id, {
      name: String(item.metadata.name ?? ""), args: (item.metadata.arguments ?? {}) as Record<string, unknown>,
    });
    const request = requests.get(id);
    if (item.event_type === "tool.result" && item.status !== "failed" && request && eligible(request.name, request.args)) {
      add(Array.isArray(item.metadata.web_images) ? item.metadata.web_images : imageReferences(item.metadata.result_preview));
    }
  }
  return images;
}
