/** Authoring is opt-in; greetings and business requests belong to the agent. */
export function isAgentConfigurationRequest(message: string): boolean {
  const text = message.trim();
  if (/^\/(?:edit|build)(?:\s|$)/i.test(text)) return true;
  const action = "(?:修改|调整|更新|配置|设置|添加|新增|删除|移除|安装|卸载|创建|生成|优化|完善|改进|重写|替换|改成|改为|change|update|configure|add|remove|install|create|rewrite|edit)";
  const target = "(?:智能体(?:配置|行为|指令|能力)?|agent(?:\\s+config(?:uration)?)?|系统提示词|提示词|system\\s*prompt|systemPrompt|AGENTS\\.md|SKILL\\.md|skills?|技能|MCP|工具配置|知识库配置|输出规则|角色设定)";
  return new RegExp(`${action}.{0,35}${target}|${target}.{0,35}${action}`, "i").test(text)
    || /^(?:请|帮我|让它|你)?\s*(?:以后|今后|始终|默认|每次)(?:都|只|用|要|不要|不再)/.test(text);
}
