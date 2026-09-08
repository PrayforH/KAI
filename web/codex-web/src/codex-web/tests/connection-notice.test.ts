import { describe, expect, it, vi } from "vitest";

import { appServerConnectionNotice } from "../connection-notice";

describe("appServerConnectionNotice", () => {
  it("失败时提供手动重连操作", () => {
    const reconnect = vi.fn();
    const notice = appServerConnectionNotice("failed", reconnect);

    expect(notice).toMatchObject({
      message: "与 Codex app-server 的连接失败。",
      description: "请检查网络或服务状态后重试。",
      actions: [{ label: "重新连接" }],
    });
    notice?.actions?.[0]?.onClick();
    expect(reconnect).toHaveBeenCalledOnce();
  });

  it("重连中显示进度，已连接时隐藏", () => {
    expect(appServerConnectionNotice("reconnecting", vi.fn())?.message).toContain("正在重新连接");
    expect(appServerConnectionNotice("connected", vi.fn())).toBeNull();
  });
});
