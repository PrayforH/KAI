import { describe, expect, it } from "vitest";

import { RECONNECT_FAILURE_AFTER_MS, reconnectDelayMs } from "../reconnect-policy";

describe("reconnectDelayMs", () => {
  it("指数退避并在五秒封顶", () => {
    expect([0, 1, 2, 3, 4, 5, 8].map(reconnectDelayMs)).toEqual([
      250,
      500,
      1000,
      2000,
      5000,
      5000,
      5000,
    ]);
  });

  it("连续重连三十秒后进入失败状态", () => {
    expect(RECONNECT_FAILURE_AFTER_MS).toBe(30_000);
  });
});
