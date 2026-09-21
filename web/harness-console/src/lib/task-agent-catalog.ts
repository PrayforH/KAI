import { agentDisplayName } from "./agent-display-name";
import { readClientResource } from "./client-read-cache";
import { isAgentVisible } from "./agent-visibility";
import { requireAuthenticatedResponse } from "./client-auth";
import type { StudioDraftSummary } from "./studio-client";

export interface TaskAgent {
  internal?: boolean;
  agentId?: string;
  name: string;
  version: string;
  displayName: string;
  domain: string;
  modelRoute?: string;
  model?: string;
  modelCapabilities?: string[];
  skills?: Array<{ name: string; description: string }>;
  ownerUserId?: string;
  scope?: "personal" | "team";
  spaceId?: string;
  spaceName?: string;
  runnableByViewer?: boolean;
  currentVersion?: string;
  connectionMode?: "caller_owned" | "service_owned";
  canView?: boolean;
  canChat?: boolean;
  canEdit?: boolean;
}

interface RuntimeAgent {
  name: string;
  version: string;
}

interface PublishedAgent {
  agent_id?: string | null;
  name: string;
  version: string;
  display_name: string;
  domain: string;
  model_route?: string | null;
  model?: string | null;
  model_capabilities?: string[];
  skills?: Array<{ name: string; description: string }>;
  owner_user_id: string;
  scope: "personal" | "team";
  space_id?: string | null;
  space_name?: string | null;
  runnable_by_viewer?: boolean;
  current_version?: string | null;
  connection_mode?: "caller_owned" | "service_owned";
  can_view?: boolean;
  can_chat?: boolean;
  can_edit?: boolean;
}

export interface TaskAgentCatalog {
  agents: TaskAgent[];
  defaultAgent: TaskAgent;
  hiddenAgents?: TaskAgent[];
}

/**
 * Stable identity of an Agent (version-independent): agentId once the
 * workspace model provides it, otherwise the coordinate without the version
 * suffix. Used to decide whether switching versions continues a thread.
 */
export function agentIdentity(agent: Pick<TaskAgent, "name" | "version">): string {
  const withId = agent as Partial<TaskAgent>;
  if (withId.agentId) return withId.agentId;
  return agentCoordinate(agent).split("@")[0];
}

/**
 * Stable key of a concrete catalog item (version-sensitive) for caching,
 * deduplication and React keys: agentId + version once available, otherwise
 * the full coordinate `scope:spaceId:ownerUserId:name@version`. `name@version`
 * alone is never a unique identity.
 */
export function agentItemKey(agent: Pick<TaskAgent, "name" | "version">): string {
  const withId = agent as Partial<TaskAgent>;
  if (withId.agentId) return `${withId.agentId}@${agent.version}`;
  return agentCoordinate(agent);
}

/** Legacy full-coordinate form: `scope:spaceId:ownerUserId:name@version`. */
export function agentCoordinate(agent: Pick<TaskAgent, "name" | "version">) {
  const scoped = agent as Pick<
    TaskAgent,
    "name" | "version" | "ownerUserId" | "spaceId" | "scope"
  >;
  if (!scoped.scope && !scoped.spaceId && !scoped.ownerUserId) {
    return `${agent.name}@${agent.version}`;
  }
  return `${scoped.scope ?? "personal"}:${scoped.spaceId ?? "-"}:${scoped.ownerUserId ?? "-"}:${agent.name}@${agent.version}`;
}

/**
 * Identity for the conversation runtime. The catalog fills in an agent's id,
 * owner and space a moment after a thread opens; keying on any of them remounted
 * the whole conversation just after it appeared. The runtime is told the name and
 * version, so those are what decide a rebuild.
 */
export function runtimeAgentKey(agent: Pick<TaskAgent, "name" | "version">): string {
  return `${agent.name}@${agent.version}`;
}

export function findTaskAgent(
  agents: readonly TaskAgent[],
  coordinates: Pick<TaskAgent, "name" | "version"> &
    Partial<Pick<TaskAgent, "agentId" | "ownerUserId" | "spaceId">>,
): TaskAgent | undefined {
  return agents.find(
    (agent) =>
      (Boolean(coordinates.agentId) &&
        agent.agentId === coordinates.agentId &&
        agent.version === coordinates.version) ||
      (agent.name === coordinates.name &&
        agent.version === coordinates.version &&
        (!coordinates.ownerUserId || agent.ownerUserId === coordinates.ownerUserId) &&
        (!coordinates.spaceId || agent.spaceId === coordinates.spaceId)),
  );
}

/** Agents the requesting user may actually chat with (task selector). */
export function chatUsableAgents(agents: readonly TaskAgent[]): TaskAgent[] {
  return agents.filter(
    (agent) => agent.canChat !== false && agent.domain !== "historical",
  );
}

function json<T>(url: string): Promise<T> {
  return readClientResource(url, () => fetchJson<T>(url));
}

async function fetchJson<T>(url: string): Promise<T> {
  const response = requireAuthenticatedResponse(
    await fetch(url, { cache: "no-store" }),
  );
  if (!response.ok) {
    throw new Error((await response.text()) || `HTTP ${response.status}`);
  }
  return response.json() as Promise<T>;
}

function studioCoordinate(
  draft: Pick<StudioDraftSummary, "name" | "version">,
  currentUserId: string | null,
): string {
  return currentUserId
    ? `personal:-:${currentUserId}:${draft.name}@${draft.version}`
    : `${draft.name}@${draft.version}`;
}

function registryCoordinate(
  agent: Pick<PublishedAgent, "name" | "version" | "owner_user_id" | "scope" | "space_id">,
  currentUserId: string | null,
): string {
  const owner = agent.owner_user_id ?? "";
  if (!currentUserId || !owner || agent.scope !== "personal") {
    return `${agent.name}@${agent.version}`;
  }
  return `personal:-:${owner}:${agent.name}@${agent.version}`;
}

export async function loadTaskAgentCatalog(
  currentUserId: string | null = null,
  showInternal = false,
): Promise<TaskAgentCatalog> {
  const [runtime, registry, drafts] = await Promise.all([
    json<RuntimeAgent>("/api/harness/runtime-config"),
    json<PublishedAgent[]>("/api/harness/agents").catch(() => {
      // Keep the configured runtime and Studio versions usable during API upgrades.
      return [];
    }),
    json<StudioDraftSummary[]>("/api/studio/drafts").catch(() => {
      // Running tasks must remain usable when Studio is temporarily unavailable.
      return [];
    }),
  ]);
  const studioVersions = drafts
    .filter(
      (draft): draft is StudioDraftSummary & { publishedVersion: string } =>
        Boolean(draft.publishedVersion),
    )
    .map((draft) => ({
      internal: Boolean(draft.parentDraftId),
      spaceId: draft.spaceId ?? undefined,
      name: draft.name,
      version: draft.publishedVersion!,
      displayName: agentDisplayName(draft.name, draft.displayName),
      domain: draft.domain,
    }));
  const studioByCoordinate = new Map(
    studioVersions.map((agent) => [
      studioCoordinate(agent, currentUserId),
      agent,
    ]),
  );
  const internalDrafts = drafts.filter((draft) => draft.parentDraftId);
  const registryVersions = registry.map((agent) => {
    const internal = internalDrafts.some((draft) => draft.agentId ? draft.agentId === agent.agent_id
      : agent.scope === "personal" && agent.owner_user_id === currentUserId && draft.name === agent.name);
    const studio = studioByCoordinate.get(
      registryCoordinate(agent, currentUserId),
    );
    const sharing = {
      internal,
      agentId: agent.agent_id ?? undefined,
      ownerUserId: agent.owner_user_id,
      scope: agent.scope,
      spaceId: agent.space_id ?? undefined,
      spaceName: agent.space_name ?? undefined,
      runnableByViewer: agent.runnable_by_viewer ?? true,
      currentVersion: agent.current_version ?? undefined,
      connectionMode: agent.connection_mode ?? "caller_owned",
      canView: agent.can_view ?? true,
      canChat: agent.can_chat ?? true,
      canEdit: agent.can_edit ?? false,
      skills: agent.skills ?? [],
    } as const;
    return (
      studio
        ? {
            ...studio,
            ...sharing,
            modelRoute: agent.model_route ?? undefined,
            model: agent.model ?? undefined,
            modelCapabilities: agent.model_capabilities ?? [],
          }
        : {
        name: agent.name,
        version: agent.version,
        displayName:
          agent.display_name === "public-opinion-agent"
            ? "舆情分析"
            : agentDisplayName(agent.name, agent.display_name),
        domain: agent.domain,
        ...sharing,
        modelRoute: agent.model_route ?? undefined,
        model: agent.model ?? undefined,
        modelCapabilities: agent.model_capabilities ?? [],
      }
    );
  });
  // Studio drafts are a resilient fallback while the published registry is
  // unavailable or rolling forward. Once the registry exposes this user's
  // personal release, keep the richer scoped record and suppress only its
  // unscoped draft projection. Other owners and team-space releases with the
  // same name/version remain distinct identities.
  const currentUserRegistryReleases = new Set(
    registryVersions
      .filter(
        (agent) =>
          Boolean(currentUserId) &&
          agent.scope === "personal" &&
          agent.ownerUserId === currentUserId,
      )
      .map((agent) => `${agent.name}@${agent.version}`),
  );
  const studioFallbackVersions = studioVersions.filter(
    (agent) =>
      !currentUserRegistryReleases.has(`${agent.name}@${agent.version}`),
  );
  const published: TaskAgent[] = [...registryVersions, ...studioFallbackVersions].filter(
    (agent, index, values) =>
      values.findIndex(
        (candidate) => agentItemKey(candidate) === agentItemKey(agent),
      ) === index,
  );
  const runtimeCandidates = published.filter(agent => agent.name === runtime.name
    && agent.scope !== "team" && agent.canChat !== false
    && (!currentUserId || !agent.ownerUserId || agent.ownerUserId === currentUserId));
  // A platform sync can publish a new immutable coordinate while the web
  // container still has an older default. Prefer the caller's current release
  // over manufacturing a coordinate which is absent from the registry.
  const runtimeMatch = runtimeCandidates.find(agent => agent.version === runtime.version)
    ?? runtimeCandidates.find(agent => agent.currentVersion === agent.version)
    ?? (runtimeCandidates.length === 1 ? runtimeCandidates[0] : undefined);
  const defaultAgent = runtimeMatch ?? {
    name: runtime.name,
    version: runtime.version,
    displayName: agentDisplayName(runtime.name),
    domain: "default",
  };
  const agents = runtimeMatch
    ? published
    : [defaultAgent, ...published];
  return {
    defaultAgent,
    hiddenAgents: agents.filter((agent) => !isAgentVisible(agent, showInternal)),
    agents: agents.filter((agent) => isAgentVisible(agent, showInternal)).filter(
      (agent, index, values) =>
        values.findIndex(
          (candidate) => agentItemKey(candidate) === agentItemKey(agent),
        ) === index,
    ),
  };
}

/** Use the platform release for new system-assistant tasks; keep team releases pinned. */
export function currentSystemAssistant(agent: TaskAgent | null, current: TaskAgent | null): TaskAgent | null {
  if (!agent) return current;
  return current && agent.name === "lead-agent" && current.name === agent.name
    && agent.scope !== "team" && !agent.spaceId && !current.spaceId
    && (!agent.ownerUserId || agent.ownerUserId === current.ownerUserId)
    ? current : agent;
}
