import type { ConnectionStatus } from "./app-server-state";

export type AppServerConnectionNotice = {
  message: string;
  description?: string;
  actions?: Array<{
    label: string;
    onClick: () => void;
    variant?: "default" | "outline" | "ghost";
  }>;
};

export function appServerConnectionNotice(
  status: ConnectionStatus,
  reconnect: () => void,
): AppServerConnectionNotice | null {
  if (status === "failed") {
    return {
      message: "与 Codex app-server 的连接失败。",
      description: "请检查网络或服务状态后重试。",
      actions: [{ label: "重新连接", onClick: reconnect }],
    };
  }
  if (status === "reconnecting") {
    return { message: "与 Codex app-server 的连接已断开，正在重新连接..." };
  }
  return null;
}
