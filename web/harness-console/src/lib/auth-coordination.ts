export type AuthInvalidationReason = "session_expired" | "session_replaced" | "account_changed";

export type AuthCoordinationEvent =
  | { type: "signed_in"; userId: string; nonce: string }
  | { type: "invalidated"; reason: AuthInvalidationReason; nonce: string };

type PublishableAuthEvent =
  | { type: "signed_in"; userId: string }
  | { type: "invalidated"; reason: AuthInvalidationReason };

const EVENT_NAME = "harness:auth-coordination";
const STORAGE_KEY = "harness-auth-coordination-event";
const CHANNEL_NAME = "harness-auth-coordination";

function isAuthEvent(value: unknown): value is AuthCoordinationEvent {
  if (!value || typeof value !== "object") return false;
  const candidate = value as Partial<AuthCoordinationEvent>;
  return candidate.type === "signed_in"
    ? typeof candidate.userId === "string"
    : candidate.type === "invalidated"
      && ["session_expired", "session_replaced", "account_changed"].includes(
        String(candidate.reason),
      );
}

export function publishAuthEvent(
  value: PublishableAuthEvent,
): void {
  if (typeof window === "undefined") return;
  const event = { ...value, nonce: `${Date.now()}:${Math.random()}` } as AuthCoordinationEvent;
  window.dispatchEvent(new CustomEvent<AuthCoordinationEvent>(EVENT_NAME, { detail: event }));
  try {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(event));
  } catch {
    // BroadcastChannel still covers browsers where localStorage is unavailable.
  }
  if (typeof BroadcastChannel !== "undefined") {
    const channel = new BroadcastChannel(CHANNEL_NAME);
    channel.postMessage(event);
    channel.close();
  }
}

export function subscribeAuthEvents(
  listener: (event: AuthCoordinationEvent) => void,
): () => void {
  if (typeof window === "undefined") return () => undefined;
  const direct = (event: Event) => {
    const value = (event as CustomEvent<unknown>).detail;
    if (isAuthEvent(value)) listener(value);
  };
  const storage = (event: StorageEvent) => {
    if (event.key !== STORAGE_KEY || !event.newValue) return;
    try {
      const value: unknown = JSON.parse(event.newValue);
      if (isAuthEvent(value)) listener(value);
    } catch {
      // Ignore malformed cross-tab values.
    }
  };
  const channel = typeof BroadcastChannel === "undefined"
    ? null
    : new BroadcastChannel(CHANNEL_NAME);
  const broadcast = (event: MessageEvent<unknown>) => {
    if (isAuthEvent(event.data)) listener(event.data);
  };
  window.addEventListener(EVENT_NAME, direct);
  window.addEventListener("storage", storage);
  channel?.addEventListener("message", broadcast);
  return () => {
    window.removeEventListener(EVENT_NAME, direct);
    window.removeEventListener("storage", storage);
    channel?.removeEventListener("message", broadcast);
    channel?.close();
  };
}
