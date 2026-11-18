import type { GroupMessage } from "./assistant-client";

/** 群消息的运行状态文案；群会话、右侧栏、聊天记录共用一套口径。 */
export const GROUP_MESSAGE_STATUS_LABEL: Record<GroupMessage["status"], string> = {
  running: "运行中",
  waiting: "等待确认",
  completed: "已完成",
  failed: "失败",
  stopped: "已停止",
};
