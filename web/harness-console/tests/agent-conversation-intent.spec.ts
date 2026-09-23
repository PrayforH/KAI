import {expect,it} from "vitest";
import {isAgentConfigurationRequest} from "../src/lib/agent-conversation-intent";
it.each(["你好","你是谁","帮我审查这份合同","修改合同里的付款条款","把这次回答改成表格","分析 AGENTS.md 有哪些安全问题","运行这个任务","修改合同简介","生成服务器配置文件"])("keeps business conversation with the agent: %s", text=>expect(isAgentConfigurationRequest(text)).toBe(false));
it.each(["修改当前智能体配置：只修改描述","把系统提示词改成中文","帮我创建一个 skill","安装这个技能","更新 AGENTS.md","以后都用中文回答","/edit 输出改成三行","修改配置：更新名称和简介","把显示名称改为会议助手","请生成两项配置修改建议：显示名称改为会议助手，简介改为会议整理","请更新智能体简介"])("routes explicit authoring: %s",text=>expect(isAgentConfigurationRequest(text)).toBe(true));
