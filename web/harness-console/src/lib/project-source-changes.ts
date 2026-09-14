import type { DeepagentsProjectComparison, ProjectSourceFile } from "./studio-client";
export type ProjectSourceChange = { path: string; status: "added" | "deleted" | "modified"; before?: ProjectSourceFile; after?: ProjectSourceFile };
export function projectSourceChanges(comparison: DeepagentsProjectComparison): ProjectSourceChange[] {
  const before = new Map(comparison.before.files.map(file => [file.path, file]));
  const after = new Map(comparison.after.files.map(file => [file.path, file]));
  return [...new Set([...before.keys(), ...after.keys()])].sort().flatMap(path => {
    const oldFile = before.get(path), newFile = after.get(path);
    if (oldFile && newFile && (oldFile.digest && newFile.digest ? oldFile.digest === newFile.digest : oldFile.content === newFile.content && oldFile.size === newFile.size)) return [];
    return [{ path, status: !oldFile ? "added" : !newFile ? "deleted" : "modified", before: oldFile, after: newFile }];
  });
}
