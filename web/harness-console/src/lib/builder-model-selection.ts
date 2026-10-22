import type { StudioDraft } from "./agent-studio";
import type { TaskModelRoute } from "./task-model-catalog";

/** The builder owns its model choice; the draft's default model is never a preference. */
export function builderModelRoutes(routes: TaskModelRoute[], runtime?: StudioDraft["runtime"]): TaskModelRoute[] {
  const format = runtime === "codex-app-server" ? "openai_compatible" : "anthropic_compatible";
  return routes.filter(route => route.modelType !== "video_generation" && (!runtime || runtime === "deepagents" || !route.apiFormat || route.apiFormat === format));
}
export function preferredBuilderModel(routes: TaskModelRoute[], selected: string | null): string | null {
  return routes.find(route => route.id === selected)?.id
    ?? routes.find(route => route.id === "deepseek-v4-flash")?.id
    ?? routes[0]?.id ?? null;
}
