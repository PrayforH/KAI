/**
 * Run an async worker over items with at most `limit` in flight at a time,
 * preserving the input order in the result. Used for knowledge document
 * uploads so a batch reaches the engine without flooding it.
 */
export async function mapWithConcurrency<T, R>(
  items: readonly T[],
  limit: number,
  worker: (item: T, index: number) => Promise<R>,
): Promise<R[]> {
  const results = new Array<R>(items.length);
  if (items.length === 0) return results;
  const size = Math.max(1, Math.min(Math.trunc(limit) || 1, items.length));
  let next = 0;
  await Promise.all(
    Array.from({ length: size }, async () => {
      for (;;) {
        const index = next;
        next += 1;
        if (index >= items.length) return;
        results[index] = await worker(items[index], index);
      }
    }),
  );
  return results;
}
