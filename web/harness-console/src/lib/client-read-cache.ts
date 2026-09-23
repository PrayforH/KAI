import { subscribeAuthEvents } from "./auth-coordination";

type Entry = { value: unknown; receivedAt: number };
const snapshots = new Map<string, Entry>();
const pending = new Map<string, Promise<unknown>>();
let subscribed = false;
let generation = 0;
const MAX_ENTRIES = 128;

function subscribe() {
  if (subscribed || typeof window === "undefined") return;
  subscribed = true;
  subscribeAuthEvents(() => invalidateClientReads());
}

/** Memory only. Auth changes and mutations detach all older in-flight reads. */
export function invalidateClientReads() {
  generation += 1;
  snapshots.clear();
  pending.clear();
}

export function forgetClientRead(key: string) {
  snapshots.delete(key);
  pending.delete(key);
}

export function forgetClientReadPrefix(prefix: string) {
  for (const key of new Set([...snapshots.keys(), ...pending.keys()])) {
    if (key.startsWith(prefix)) forgetClientRead(key);
  }
}

/** Cancel this caller's wait without cancelling a shared read used by another view. */
export function waitForClientRead<T>(promise: Promise<T>, signal?: AbortSignal): Promise<T> {
  if (!signal) return promise;
  return new Promise<T>((resolve, reject) => {
    const abort = () => reject(signal.reason ?? new DOMException("Aborted", "AbortError"));
    if (signal.aborted) abort();
    else signal.addEventListener("abort", abort, {once: true});
    promise.then(value => {if (!signal.aborted) resolve(value);}, reject)
      .finally(() => signal.removeEventListener("abort", abort));
  });
}

export function peekClientRead<T>(key: string): T | undefined {
  return snapshots.get(key)?.value as T | undefined;
}

export function rememberClientRead<T>(key: string, value: T): T {
  subscribe();
  snapshots.delete(key);
  snapshots.set(key, { value, receivedAt: Date.now() });
  if (snapshots.size > MAX_ENTRIES) snapshots.delete(snapshots.keys().next().value!);
  return value;
}

export function readClientResource<T>(
  key: string, loader: () => Promise<T>, maxAgeMs = 10_000,
): Promise<T> {
  subscribe();
  const cached = snapshots.get(key);
  if (cached && Date.now() - cached.receivedAt < maxAgeMs) {
    return Promise.resolve(cached.value as T);
  }
  const existing = pending.get(key);
  if (existing) return existing as Promise<T>;
  const startedGeneration = generation;
  const request = loader().then((value) => {
    if (generation === startedGeneration && pending.get(key) === request && maxAgeMs > 0) {
      rememberClientRead(key, value);
    }
    return value;
  }).finally(() => {
    if (pending.get(key) === request) pending.delete(key);
  });
  pending.set(key, request);
  return request;
}

/** Invalidate both before dispatch and after completion, including uncertain failures. */
export async function mutateClientResource<T>(operation: () => Promise<T>): Promise<T> {
  invalidateClientReads();
  try { return await operation(); }
  finally { invalidateClientReads(); }
}
