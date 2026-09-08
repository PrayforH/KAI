import { requireAuthenticatedResponse } from "./client-auth";

export interface TaskModelRoute {
  id: string;
  label: string;
  provider: string;
  model: string;
  capabilities: string[];
  modelType: "chat" | "vision" | "video_generation";
}

interface CapabilityResponse {
  modelRoutes: Array<{
    routeId: string;
    label: string;
    provider: string;
    models: string[];
    capabilities: string[];
    modelType?: "chat" | "vision" | "image_generation" | "video_generation";
    enabled: boolean;
  }>;
}

export async function loadTaskModelRoutes(): Promise<TaskModelRoute[]> {
  const response = requireAuthenticatedResponse(
    await fetch("/api/studio/capabilities", { cache: "no-store" }),
  );
  if (!response.ok) {
    throw new Error((await response.text()) || `HTTP ${response.status}`);
  }
  const catalog = (await response.json()) as CapabilityResponse;
  return catalog.modelRoutes
    // A task override is a route ID, so selectable routes must resolve to one
    // unambiguous provider model. Video routes use the dedicated composer
    // path and never reach the conversational Agent runtime.
    // Keep retired platform routes out during rolling upgrades where an older
    // API instance may still return them from its durable catalog projection.
    .filter(
      (route) =>
        route.routeId !== "anthropic-official" &&
        route.routeId !== "new-api-default" &&
        route.enabled &&
        route.models.length === 1 &&
        (route.modelType === undefined || ["chat", "vision", "video_generation"].includes(route.modelType)) &&
        !route.capabilities.includes("image_generation") &&
        (route.modelType === "video_generation" || !route.capabilities.includes("video_generation")),
    )
    .map((route) => ({
      id: route.routeId,
      label: route.label,
      provider: route.provider,
      model: route.models[0],
      capabilities: route.capabilities,
      modelType: route.modelType === "video_generation"
        ? "video_generation"
        : route.modelType === "vision" || route.capabilities.includes("vision")
          ? "vision"
          : "chat",
    }));
}

const storagePrefix = "agent-studio.task-model:";

export function loadTaskModelOverride(
  storage: Pick<Storage, "getItem">,
  threadId: string,
): string | null {
  const value = storage.getItem(`${storagePrefix}${threadId}`);
  return value && /^[a-z][a-z0-9-]{0,63}$/.test(value) ? value : null;
}

export function saveTaskModelOverride(
  storage: Pick<Storage, "setItem" | "removeItem">,
  threadId: string,
  routeId: string | null,
) {
  const key = `${storagePrefix}${threadId}`;
  if (routeId) storage.setItem(key, routeId);
  else storage.removeItem(key);
}
