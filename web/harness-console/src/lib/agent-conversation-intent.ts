/** Authoring is opt-in; greetings and business requests belong to the agent. */
export function isAgentConfigurationRequest(message: string): boolean {
  const text = message.trim();
  if (/^\/(?:edit|build)(?:\s|$)/i.test(text)) return true;
  if (/^(?:请|帮我|请帮我)?\s*(?:修改|调整|更新|设置)\s*(?:当前)?配置(?:[：:，,\s]|$)/.test(text)) return true;
  // Config switches differ from a business request to search the web once.
  const network = "(?:联网(?:功能|能力)?|网络搜索|网页读取|WebSearch|WebFetch|web\\s*(?:search|access))";
  const toggle = "(?:启用|开启|打开|关闭|禁用|停用|允许|禁止|加上|enable|disable)";
  if (new RegExp(`${toggle}.{0,12}${network}|${network}.{0,12}${toggle}`, "i").test(text)) return true;
  const action = "(?:启用|开启|关闭|禁用|停用|绑定|解绑|切换|修改|调整|更新|配置|设置|添加|新增|删除|移除|安装|卸载|创建|生成|优化|完善|改进|重写|替换|改成|改为|enable|disable|bind|change|update|configure|add|remove|install|create|rewrite|edit)";
  const target = "(?:智能体(?:配置|行为|指令|能力)?|agent(?:\\s+config(?:uration)?)?|系统提示词|提示词|system\\s*prompt|systemPrompt|AGENTS\\.md|SKILL\\.md|skills?|技能|MCP|工具配置|知识库|内置工具|Python 算子|Python算子|子智能体|协作角色|subagents?|模型|运行时|执行环境|权限策略|超时时间|输出规则|角色设定|显示名称|智能体简介|配置修改建议|配置变更)";
  return new RegExp(`${action}.{0,35}${target}|${target}.{0,35}${action}`, "i").test(text)
    || /^(?:请|帮我|让它|你)?\s*(?:以后|今后|始终|默认|每次)(?:都|只|用|要|不要|不再)/.test(text);
}
