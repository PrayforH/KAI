import type { RunToolNode } from "./run-view-model";

const toolTitles: Record<string, string> = {
  Glob: "查找文件",
  Grep: "搜索内容",
  Read: "读取文件",
  Write: "创建文件",
  Edit: "编辑文件",
  Bash: "运行命令",
  WebSearch: "搜索网页",
  WebFetch: "读取网页",
};

const argumentLabels: Record<string, string> = {
  file_path: "文件",
  path: "范围",
  pattern: "模式",
  glob: "文件模式",
  query: "查询",
  url: "地址",
  urls: "地址",
  command: "命令",
  description: "任务",
  offset: "起始行",
  limit: "数量",
  output_mode: "输出",
};

const preferredArgumentKeys: Record<string, string[]> = {
  Glob: ["pattern", "glob", "path"],
  Grep: ["pattern", "path", "glob", "output_mode"],
  Read: ["file_path", "path", "offset", "limit"],
  Write: ["file_path", "path"],
  Edit: ["file_path", "path"],
  Bash: ["command", "description"],
  WebSearch: ["query"],
  WebFetch: ["url"],
};

const hiddenArgumentKeys = new Set([
  "content",
  "old_string",
  "new_string",
  "password",
  "passwd",
  "secret",
  "token",
]);

function compactText(value: unknown): string | undefined {
  if (typeof value === "string") {
    const compact = value.replace(/\s+/g, " ").trim();
    if (!compact) return undefined;
    return compact.length > 160 ? `${compact.slice(0, 159)}…` : compact;
  }
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  if (Array.isArray(value)) {
    const items = value
      .slice(0, 3)
      .map(compactText)
      .filter((item): item is string => Boolean(item));
    if (items.length === 0) return undefined;
    return `${items.join("、")}${value.length > 3 ? ` 等 ${value.length} 项` : ""}`;
  }
  return undefined;
}

export function toolTitle(name: string) {
  if (toolTitles[name]) return toolTitles[name];
  if (name.endsWith("__tavily_search")) return "搜索网页";
  if (name.endsWith("__tavily_extract")) return "提取网页";
  return `调用 ${name}`;
}

export function toolArgumentFacts(tool: Pick<RunToolNode, "name" | "arguments">) {
  const args = tool.arguments;
  if (!args) return [];
  const preferred = preferredArgumentKeys[tool.name] ?? [];
  const orderedKeys = [
    ...preferred,
    ...Object.keys(args).filter(
      (key) => !preferred.includes(key) && !hiddenArgumentKeys.has(key),
    ),
  ];
  const seen = new Set<string>();
  const facts: string[] = [];
  for (const key of orderedKeys) {
    if (seen.has(key) || hiddenArgumentKeys.has(key)) continue;
    seen.add(key);
    const value = compactText(args[key]);
    if (!value) continue;
    facts.push(`${argumentLabels[key] ?? key} ${value}`);
    if (facts.length === 4) break;
  }
  return facts;
}

function argumentText(
  tool: Pick<RunToolNode, "arguments">,
  ...keys: string[]
) {
  for (const key of keys) {
    const value = compactText(tool.arguments?.[key]);
    if (value) return value;
  }
  return undefined;
}

export function toolActivitySentence(
  tool: Pick<RunToolNode, "name" | "arguments" | "status">,
) {
  const path = argumentText(tool, "file_path", "path");
  const pattern = argumentText(tool, "pattern", "glob");
  const active = tool.status === "running" || tool.status === "waiting";
  switch (tool.name) {
    case "Glob":
      return pattern
        ? `${active ? "正在查找" : "已查找"}文件 ${pattern}${path ? `，范围 ${path}` : ""}`
        : `${active ? "正在查找" : "已查找"}文件${path ? `，范围 ${path}` : ""}`;
    case "Grep":
      return pattern
        ? active
          ? `正在 ${path ?? "工作区"} 中搜索“${pattern}”`
          : `已在 ${path ?? "工作区"} 中搜索“${pattern}”`
        : `${active ? "正在搜索" : "已搜索"}内容${path ? `，范围 ${path}` : ""}`;
    case "Read":
      return path
        ? `${active ? "正在读取" : "已读取"} ${path}`
        : active ? "正在读取文件" : "已读取文件";
    case "Write":
      return path
        ? `${active ? "正在创建" : "已创建"} ${path}`
        : active ? "正在创建文件" : "已创建文件";
    case "Edit":
      return path
        ? `${active ? "正在编辑" : "已编辑"} ${path}`
        : active ? "正在编辑文件" : "已编辑文件";
    case "Bash": {
      const command = argumentText(tool, "command", "description");
      return command
        ? `${active ? "正在运行" : "已运行"} ${command}`
        : active ? "正在运行命令" : "已运行命令";
    }
    case "WebSearch": {
      const query = argumentText(tool, "query");
      return query
        ? `${active ? "正在搜索" : "已搜索"}“${query}”`
        : active ? "正在搜索网页" : "已搜索网页";
    }
    case "WebFetch": {
      const url = argumentText(tool, "url");
      return url
        ? `${active ? "正在读取" : "已读取"} ${url}`
        : active ? "正在读取网页" : "已读取网页";
    }
    default: {
      const query = argumentText(tool, "query");
      const url = argumentText(tool, "url");
      if (tool.name.endsWith("__tavily_search")) {
        return query
          ? `${active ? "正在搜索" : "已搜索"}“${query}”`
          : active ? "正在搜索网页" : "已搜索网页";
      }
      if (tool.name.endsWith("__tavily_extract")) {
        return url
          ? `${active ? "正在提取" : "已提取"} ${url}`
          : active ? "正在提取网页" : "已提取网页";
      }
      const facts = toolArgumentFacts(tool);
      return facts.length > 0
        ? `${active ? "正在调用" : "已调用"} ${tool.name} · ${facts.slice(0, 2).join(" · ")}`
        : `${active ? "正在调用" : "已调用"} ${tool.name}`;
    }
  }
}

export function toolBatchTitle(tools: readonly Pick<RunToolNode, "name">[]) {
  const names = new Set(tools.map((tool) => tool.name));
  if ([...names].every((name) => ["Glob", "Grep", "Read"].includes(name))) {
    return "已读取文件";
  }
  if ([...names].every((name) => ["Write", "Edit"].includes(name))) {
    return "已修改文件";
  }
  if ([...names].every((name) => name === "Bash")) return "已运行命令";
  if (
    [...names].every(
      (name) =>
        name === "WebSearch" ||
        name === "WebFetch" ||
        name.endsWith("__tavily_search") ||
        name.endsWith("__tavily_extract"),
    )
  ) {
    return "已访问网页";
  }
  return `已处理 ${tools.length} 项`;
}
