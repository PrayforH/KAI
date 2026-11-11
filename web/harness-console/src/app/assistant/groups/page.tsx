import { redirect } from "next/navigation";

/**
 * 旧的独立群聊页只保留地址兼容：群会话已并入主界面聊天区，
 * 这里带上群 id 重定向过去，不再维护另一套页面布局。
 */
export default async function AssistantGroupsRedirect({
  searchParams,
}: {
  searchParams: Promise<{ group?: string | string[] }>;
}) {
  const { group } = await searchParams;
  const groupId = Array.isArray(group) ? group[0] : group;
  redirect(groupId ? `/assistant?group=${encodeURIComponent(groupId)}` : "/assistant");
}
