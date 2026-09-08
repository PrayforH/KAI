import { createRandomId } from "./random-id";

const THREAD_STORAGE_KEY = "harness-console-thread";
const THREAD_AGENT_STORAGE_PREFIX = "harness-console-thread-agent:";

export interface ThreadStorage {
  getItem(key: string): string | null;
  setItem(key: string, value: string): void;
  removeItem(key: string): void;
}

const USER_STORAGE_PREFIX = "harness-console-user:";

export function createUserScopedStorage(
  storage: ThreadStorage,
  userId: string,
): ThreadStorage {
  const prefix = `${USER_STORAGE_PREFIX}${encodeURIComponent(userId)}:`;
  return {
    getItem: (key) => storage.getItem(`${prefix}${key}`),
    setItem: (key, value) => storage.setItem(`${prefix}${key}`, value),
    removeItem: (key) => storage.removeItem(`${prefix}${key}`),
  };
}

export interface ThreadAgentBinding {
  agentId?: string;
  name: string;
  version: string;
  displayName?: string;
  domain?: string;
  ownerUserId?: string;
  scope?: "personal" | "team";
  spaceId?: string;
  spaceName?: string;
  currentVersion?: string;
  connectionMode?: "caller_owned" | "service_owned";
  canChat?: boolean;
}

type IdFactory = () => string;

export function loadOrCreateThread(
  storage: ThreadStorage,
  createId: IdFactory = createRandomId,
): string {
  const existing = storage.getItem(THREAD_STORAGE_KEY);
  return existing ?? createNewThread(storage, createId);
}

export function createNewThread(
  storage: ThreadStorage,
  createId: IdFactory = createRandomId,
): string {
  const threadId = createId();
  storage.setItem(THREAD_STORAGE_KEY, threadId);
  return threadId;
}

export function selectThread(storage: ThreadStorage, threadId: string): string {
  storage.setItem(THREAD_STORAGE_KEY, threadId);
  return threadId;
}

export function bindThreadAgent(
  storage: ThreadStorage,
  threadId: string,
  agent: ThreadAgentBinding,
): void {
  storage.setItem(
    `${THREAD_AGENT_STORAGE_PREFIX}${threadId}`,
    JSON.stringify(agent),
  );
}

export function loadThreadAgent(
  storage: ThreadStorage,
  threadId: string,
): ThreadAgentBinding | null {
  const raw = storage.getItem(`${THREAD_AGENT_STORAGE_PREFIX}${threadId}`);
  if (!raw) return null;
  try {
    const value = JSON.parse(raw) as Partial<ThreadAgentBinding>;
    if (
      typeof value.name !== "string" ||
      !value.name ||
      typeof value.version !== "string" ||
      !value.version
    ) {
      return null;
    }
    return {
      name: value.name,
      version: value.version,
      ...(typeof value.agentId === "string" && value.agentId
        ? { agentId: value.agentId }
        : {}),
      ...(typeof value.displayName === "string"
        ? { displayName: value.displayName }
        : {}),
      ...(typeof value.domain === "string" ? { domain: value.domain } : {}),
      ...(typeof value.ownerUserId === "string" ? { ownerUserId: value.ownerUserId } : {}),
      ...(value.scope === "personal" || value.scope === "team" ? { scope: value.scope } : {}),
      ...(typeof value.spaceId === "string" ? { spaceId: value.spaceId } : {}),
      ...(typeof value.spaceName === "string" ? { spaceName: value.spaceName } : {}),
      ...(typeof value.currentVersion === "string"
        ? { currentVersion: value.currentVersion }
        : {}),
      ...(value.connectionMode === "caller_owned" || value.connectionMode === "service_owned"
        ? { connectionMode: value.connectionMode }
        : {}),
      ...(typeof value.canChat === "boolean" ? { canChat: value.canChat } : {}),
    };
  } catch {
    return null;
  }
}
