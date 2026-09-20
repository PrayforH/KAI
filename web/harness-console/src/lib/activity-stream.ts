import { EventType, type BaseEvent, type ActivitySnapshotEvent, type ActivityDeltaEvent } from "@ag-ui/client";
import { Observable } from "rxjs";
import { activityItemSchema, runActivitySchema, type ActivityItem, type RunActivity } from "./activity-schema";

const STREAM_KEYS: Record<string, string> = {
  "reasoning.delta": "item_id",
  "reasoning.summary.delta": "item_id",
  "message.delta": "message_id",
};

function appendItem(items: ActivityItem[], item: ActivityItem) {
  const last = items.at(-1);
  const key = STREAM_KEYS[item.event_type];
  if (key && last?.event_type === item.event_type &&
      typeof item.metadata[key] === "string" && item.metadata[key] === last.metadata[key]) {
    // Match the server's durable-history projection; preserve the first id and
    // sequence so streaming rows never remount when a token is appended.
    items[items.length - 1] = { ...last, summary: (last.summary ?? "") + (item.summary ?? "") };
  } else {
    items.push(item);
  }
}

/** Compact before AG-UI clones messages/applyPatch, not just before React paints.
 * Thousands of token-sized activity items otherwise cause quadratic copies of
 * the entire conversation. Only presentation is compacted; the server keeps
 * the original events. Tool/answer/terminal events always flush in order.
 */
export function compactActivityStream(source: Observable<BaseEvent>): Observable<BaseEvent> {
  return new Observable((subscriber) => {
    let current: RunActivity | undefined;
    let envelope: ActivitySnapshotEvent | undefined;
    let pending = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const flush = () => {
      if (timer !== undefined) clearTimeout(timer);
      timer = undefined;
      if (!pending || !current || !envelope) return;
      pending = false;
      subscriber.next({ ...envelope, replace: true, content: current });
    };
    const subscription = source.subscribe({
      next(event) {
        if (event.type === EventType.ACTIVITY_SNAPSHOT) {
          flush();
          const snapshot = event as ActivitySnapshotEvent;
          const parsed = snapshot.activityType === "harness.run.v1"
            ? runActivitySchema.safeParse(snapshot.content) : undefined;
          if (parsed?.success) {
            const items: ActivityItem[] = [];
            for (const item of parsed.data.items) appendItem(items, item);
            current = { ...parsed.data, items };
            envelope = snapshot;
            subscriber.next({ ...snapshot, content: current });
          } else {
            if (snapshot.activityType === "harness.run.v1") { current = undefined; envelope = undefined; }
            subscriber.next(event);
          }
          return;
        }
        if (event.type === EventType.ACTIVITY_DELTA && current && envelope) {
          const delta = event as ActivityDeltaEvent;
          if (delta.activityType === "harness.run.v1" && delta.messageId === envelope.messageId) {
            const next = { ...current, items: [...current.items], metrics: { ...current.metrics } };
            let supported = true;
            let urgent = false;
            for (const patch of delta.patch) {
              if (patch.op === "add" && patch.path === "/items/-") {
                const parsed = activityItemSchema.safeParse(patch.value);
                if (!parsed.success) { supported = false; break; }
                appendItem(next.items, parsed.data);
                urgent ||= !STREAM_KEYS[parsed.data.event_type];
              } else if (["add", "replace"].includes(patch.op) && patch.path === "/status" && typeof patch.value === "string") {
                next.status = patch.value;
                urgent = true;
              } else if (["add", "replace"].includes(patch.op) && /^\/metrics\/[^/]+$/.test(patch.path)) {
                next.metrics[patch.path.slice(9).replaceAll("~1", "/").replaceAll("~0", "~")] = patch.value;
              } else { supported = false; break; }
            }
            if (supported) {
              current = next;
              pending = true;
              if (urgent) flush();
              else if (timer === undefined) timer = setTimeout(flush, 50);
              return;
            }
            // Future protocol operations stay authoritative. Stop compacting
            // until a fresh snapshot gives us a known state again.
            flush();
            current = undefined;
            envelope = undefined;
          }
        }
        flush();
        subscriber.next(event);
      },
      error(error) { flush(); subscriber.error(error); },
      complete() { flush(); subscriber.complete(); },
    });
    return () => {
      if (timer !== undefined) clearTimeout(timer);
      subscription.unsubscribe();
    };
  });
}
