import { handoverStore } from "../../src/lib/handover-store";

export function emitHandover(payload: Record<string, unknown>) {
  handoverStore.ingest({
    handover_id: `handover-${payload.specialist_id}`,
    run_id: "run-ui-test",
    created_at: "2026-11-02T09:00:00Z",
    updated_at: "2026-11-02T09:01:00Z",
    ...payload,
  });
}
