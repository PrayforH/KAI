/**
 * 站内下载链接归一化。
 *
 * 服务端返回的是直连 API 的规范前缀 `/v1/...`，而浏览器侧只代理 `/api/...`，
 * 直接把 `/v1/...` 当 href 用会落到 Next 的 404 页。资料库文件与消息附件
 * 都走这一处转换，避免各写一遍前缀规则。
 *
 * - `/v1/...` → `/api/...`
 * - 已是 `/api/...` 或 `http(s)://` 外链：原样返回
 * - 空值或其它未知形态：空值返回 null（调用方不渲染可点链接），其余原样返回
 */
export function resolveDownloadUrl(value: string | null | undefined): string | null {
  if (!value) return null;
  const url = value.trim();
  if (!url) return null;
  if (url.startsWith("/v1/")) return `/api/${url.slice("/v1/".length)}`;
  return url;
}
