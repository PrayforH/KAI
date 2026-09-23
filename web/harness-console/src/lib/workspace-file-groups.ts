export interface WorkspaceFileLike {
  artifact_id: string;
  name: string;
  media_type: string;
  group?: string;
}
export type FileCategory = "最终产出" | "输入文件" | "中间文件" | "技能资源" | "智能体配置";
export interface FileGroup<T> { name: FileCategory; folders: Map<string, T[]> }
const imagePattern = /\.(png|jpe?g|webp|gif|tiff?|bmp)$/i;

/** Group views only: never move, delete or relabel stored artifacts. */
export function groupWorkspaceFiles<T extends WorkspaceFileLike>(files: readonly T[]): FileGroup<T>[] {
  const order: FileCategory[] = ["最终产出", "输入文件", "中间文件", "技能资源", "智能体配置"];
  const groups = new Map<FileCategory, Map<string, T[]>>();
  for (const file of files) {
    const path = file.name.replaceAll("\\", "/").replace(/^对话文件\//, "");
    const parts = path.split("/");
    const directory = parts.slice(0, -1).join("/");
    let category: FileCategory = "最终产出";
    let folder = directory;
    if (/^(?:skills|\.skills|\.claude\/skills)\//i.test(path) || file.group?.startsWith("技能")) {
      category = "技能资源"; folder = directory.replace(/^(?:\.claude\/)?\.?skills\//i, "");
    } else if (/^(?:inputs\/original|uploads|input)\//i.test(path)) {
      category = "输入文件"; folder = directory.replace(/^(?:inputs\/original|uploads|input)\/?/i, "");
    } else if (
      /(^|\/)(?:intermediate|scratch|tmp|temp|\.cache|inputs\/derived|processed|ocr|extracted)(?:\/|$)/i.test(path)
      || (imagePattern.test(path) && /(^|\/)(?:images?_cv\w*|pdf[-_]?(?:images|pages)|pages?|rendered(?:[-_]pages)?|extracted[-_]images)(?:\/|$)/i.test(path))
    ) {
      category = "中间文件";
    } else if (file.artifact_id.startsWith("source:")) {
      category = "智能体配置";
    }
    const folders = groups.get(category) ?? new Map<string, T[]>();
    const entries = folders.get(folder) ?? [];
    entries.push(file); folders.set(folder, entries); groups.set(category, folders);
  }
  return order.flatMap(name => {
    const folders = groups.get(name);
    if (!folders) return [];
    return [{ name, folders: new Map([...folders].sort(([a], [b]) => a.localeCompare(b, "zh-CN"))) }];
  });
}
