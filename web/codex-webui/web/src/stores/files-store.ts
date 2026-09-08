/**
 * Zustand store for file browser UI state only.
 * REST data (tree, content, metadata) is managed by TanStack Query.
 */
import { create } from 'zustand';

interface FilesState {
  /** Current root directory (from thread cwd or home). */
  rootDir: string | null;
  /** Currently selected file path. */
  selectedFile: string | null;
  /** Whether the file panel is visible. */
  panelOpen: boolean;
  /** Expanded directory paths for tree state. */
  expandedDirs: Set<string>;
  /** Mtime of currently open file (for conflict detection). */
  fileMtime: number | null;

  setRootDir: (dir: string | null) => void;
  selectFile: (filePath: string | null) => void;
  setPanelOpen: (open: boolean) => void;
  toggleDirectory: (dirPath: string) => void;
  setFileMtime: (mtime: number | null) => void;
  navigateUp: () => void;
}

export const useFilesStore = create<FilesState>((set, get) => ({
  rootDir: null,
  selectedFile: null,
  panelOpen: false,
  expandedDirs: new Set<string>(),
  fileMtime: null,

  setRootDir: (dir: string | null) => {
    if (dir === get().rootDir) return;
    set({
      rootDir: dir,
      selectedFile: null,
      expandedDirs: new Set<string>(),
      fileMtime: null,
    });
  },

  selectFile: (filePath: string | null) => {
    set({ selectedFile: filePath, panelOpen: filePath !== null, fileMtime: null });
  },

  setPanelOpen: (open: boolean) => set({ panelOpen: open }),

  toggleDirectory: (dirPath: string) => {
    set((s) => {
      const next = new Set(s.expandedDirs);
      if (next.has(dirPath)) {
        next.delete(dirPath);
      } else {
        next.add(dirPath);
      }
      return { expandedDirs: next };
    });
  },

  setFileMtime: (mtime: number | null) => set({ fileMtime: mtime }),

  navigateUp: () => {
    const { rootDir } = get();
    if (!rootDir || rootDir === '/') return;
    const parent = rootDir.substring(0, rootDir.lastIndexOf('/')) || '/';
    get().setRootDir(parent);
  },
}));
