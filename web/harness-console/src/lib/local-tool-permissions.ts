import type { Specialist } from "./assistant-client";

/** 固定 4 项本地能力（与 Grok 文案族对齐的 KAI 语义，键名稳定）。 */
export const LOCAL_TOOL_PERMISSIONS = [
  { key: "run_commands", label: "在共享电脑上运行命令" },
  { key: "access_private_files", label: "访问私人文件" },
  { key: "use_connected_apps", label: "使用已连接的应用" },
  { key: "run_background_tasks", label: "运行后台/例行任务" },
] as const;

export type LocalToolKey = (typeof LOCAL_TOOL_PERMISSIONS)[number]["key"];

const KNOWN_KEYS = new Set<string>(LOCAL_TOOL_PERMISSIONS.map((item) => item.key));

function stringList(value: unknown): string[] {
  if (!Array.isArray(value)) return [];
  return value.filter(
    (item): item is string => typeof item === "string" && KNOWN_KEYS.has(item),
  );
}

/** config.local_tool_permissions：只存已允许的键；未知值忽略。 */
export function grantedLocalTools(specialist: Specialist): LocalToolKey[] {
  const raw = (specialist.config as { local_tool_permissions?: unknown })?.local_tool_permissions;
  return [...new Set(stringList(raw))] as LocalToolKey[];
}

/** config.required_local_tools：声明需要的能力；未授予的即为待授权项。 */
export function requiredLocalTools(specialist: Specialist): LocalToolKey[] {
  const raw = (specialist.config as { required_local_tools?: unknown })?.required_local_tools;
  return [...new Set(stringList(raw))] as LocalToolKey[];
}

export function pendingLocalTools(specialist: Specialist): LocalToolKey[] {
  const granted = new Set(grantedLocalTools(specialist));
  return requiredLocalTools(specialist).filter((key) => !granted.has(key));
}

export function localToolLabel(key: string): string {
  return LOCAL_TOOL_PERMISSIONS.find((item) => item.key === key)?.label ?? key;
}
