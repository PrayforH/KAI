import { expect, it } from "vitest";
import { groupWorkspaceFiles } from "../src/lib/workspace-file-groups";
const file = (name: string, artifact_id = name) => ({ artifact_id, name, media_type: name.endsWith('.png') ? 'image/png' : 'text/plain' });
it("separates scanned PDF pages and skills without treating delivered pictures as intermediate", () => {
  const files = [file('images_cv12/p01.png'), file('images_cv3/p02.png'), file('简历整理.md'), file('charts/revenue.png'), file('inputs/original/resume.pdf'), file('skills/report/SKILL.md','source:skill'), file('AGENTS.md','source:AGENTS.md')];
  const groups = groupWorkspaceFiles(files);
  expect(groups.map(group => group.name)).toEqual(['最终产出','输入文件','中间文件','技能资源','智能体配置']);
  expect([...groups[0].folders.values()].flat().map(item => item.name)).toEqual(['简历整理.md','charts/revenue.png']);
  expect([...groups[2].folders.keys()]).toEqual(['images_cv12','images_cv3']);
  expect([...groups.flatMap(group => [...group.folders.values()].flat())]).toHaveLength(files.length);
  expect(files[0].name).toBe('images_cv12/p01.png');
});
it("classifies Builder artifacts using the same paths as the main conversation", () => {
  const groups = groupWorkspaceFiles([file('对话文件/pdf_pages/p01.png'), file('对话文件/report.html')]);
  expect(groups[0].name).toBe('最终产出');
  expect(groups[1].name).toBe('中间文件');
});
