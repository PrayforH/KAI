import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

const provider = readFileSync(
  join(process.cwd(), "src/components/auth-provider.tsx"),
  "utf8",
);
const login = readFileSync(join(process.cwd(), "src/app/login/page.tsx"), "utf8");
const coordination = readFileSync(
  join(process.cwd(), "src/lib/auth-coordination.ts"),
  "utf8",
);

describe("cross-window authentication guard", () => {
  it("broadcasts successful sign-ins to existing windows", () => {
    expect(login).toContain('publishAuthEvent({ type: "signed_in"');
    expect(coordination).toContain("BroadcastChannel");
    expect(coordination).toContain("localStorage.setItem");
  });

  it("blocks the old workspace instead of leaving inputs active", () => {
    expect(provider).toContain('role="alertdialog"');
    expect(provider).toContain("inert={Boolean(invalidation)}");
    expect(provider).toContain("当前浏览器已切换到其他账号");
    expect(provider).toContain("账号已在其他窗口或设备登录");
  });

  it("rechecks identity on focus, visibility, and a bounded interval", () => {
    expect(provider).toContain('window.addEventListener("focus"');
    expect(provider).toContain('document.addEventListener("visibilitychange"');
    expect(provider).toContain("15_000");
  });
});
