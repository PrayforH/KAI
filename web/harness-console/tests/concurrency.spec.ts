import { describe, expect, it } from "vitest";
import { mapWithConcurrency } from "../src/lib/concurrency";

const tick = () => new Promise((resolve) => setTimeout(resolve, 1));

describe("mapWithConcurrency", () => {
  it("never runs more than the limit at once", async () => {
    let inFlight = 0;
    let peak = 0;
    const items = Array.from({ length: 9 }, (_, index) => index);

    const results = await mapWithConcurrency(items, 3, async (item) => {
      inFlight += 1;
      peak = Math.max(peak, inFlight);
      await tick();
      inFlight -= 1;
      return item * 2;
    });

    expect(peak).toBe(3);
    expect(results).toEqual(items.map((item) => item * 2));
  });

  it("keeps the input order even when work finishes out of order", async () => {
    const results = await mapWithConcurrency([3, 1, 2], 3, async (item) => {
      await new Promise((resolve) => setTimeout(resolve, item * 3));
      return item;
    });
    expect(results).toEqual([3, 1, 2]);
  });

  it("handles empty input and clamps silly limits", async () => {
    expect(await mapWithConcurrency([], 3, async () => 1)).toEqual([]);
    let peak = 0;
    let inFlight = 0;
    await mapWithConcurrency([1, 2], 0, async (item) => {
      inFlight += 1;
      peak = Math.max(peak, inFlight);
      await tick();
      inFlight -= 1;
      return item;
    });
    expect(peak).toBe(1);
  });
});
