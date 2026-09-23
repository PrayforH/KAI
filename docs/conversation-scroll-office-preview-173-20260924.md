# 173：连续工具读取、输出跟随与 Office 侧栏预览

代码提交：`a145d11d`（滚动与处理行）、`57c33b25`（Office 预览）。目标分支 `auto/agent-evolution`，环境 `172.20.109.173:3302`。

## 已修复

- 连续 Read 请求原来用组内所有工具 ID 拼接 React key。每加入一个工具都会重建处理行，且第一个结果到达时根节点由 div 变成 details。现在以首个事件标识固定分组，始终保留 details/summary 节点；移除处理行扫光，保留用户展开状态。
- 对话视口采用单一应用层滚动控制，观察消息根节点、输入区与视口的尺寸变化，合并至每个动画帧跟随。手动向上滚轮、触摸、键盘/滚动条操作暂停跟随；回到底部、点击向下按钮、开始新一轮恢复。嵌套思考/工具结果区的滚动独立，历史前插保留位置。主对话、Builder、效果验证共用 ConversationThread。
- 原侧栏没有 Office 类型分支，统一落入“不支持”。新增 XLSX 工作表选择与行列预览、DOCX 文档排版、PPTX 多页排版。按需加载解析器，从原有鉴权下载接口读文件。Office 渲染在禁止脚本、禁止外部资源的独立 iframe 中，避免影响主界面。
- PPTX 预览器自带的固定高度会把后续页面藏进内层滚动区，已改为连续页面随整个预览区滚动。

## 验证

- 9 个测试文件、81 项测试通过；Next.js webpack 生产构建和 TypeScript 检查通过。
- 4 次连续文件请求/结果的 DOM 回归：同一个 details 和 summary，展开状态持续保留，完成事件不重建节点。
- 浏览器使用真实 ConversationThread + 本地流式 adapter：流式输出第 49 行时底部间距 0；手动上翻后 scrollTop 持续保持 244.5，内容继续增长至 3044；点击向下按钮后底部间距恢复 0。
- 浏览器使用真实 XLSX、DOCX、PPTX：两张工作表切换；Word 中文标题、正文、表格、嵌入图片；两页幻灯片连续排列、图片成功解码。嵌入图片 naturalWidth 均为 300。
- QA 样例保留于本机 `/tmp/agent-studio-office-preview-fixtures/`，仅合成内容。

## 范围

- 支持 `.xlsx/.docx/.pptx`。旧 `.xls/.doc/.ppt` 明确提示另存为现代格式；不声称支持旧二进制格式。
- Excel 展示文本/日期/缓存公式结果，未缓存公式展示公式文本；预览每张表前 200 行、60 列，并显示总量及截断提示，不执行公式。
- 预览文件上限 50 MB，解析前限制 ZIP 声明展开总量 150 MB 与条目数 10000。加密或损坏文件可下载处理。
- 浏览器 Office 排版不提供编辑、宏执行或 PowerPoint 动画播放。
- 渲染器：docx-preview 0.4.1（https://github.com/VolodymyrBaydalka/docxjs）、pptx-preview 1.0.7（https://github.com/501351981/pptx-preview），ExcelJS 沿用已有依赖。

## 部署

本次只更新 173 的 Web，保留 API/Worker `evolution-builder-a5f86edb`。Web 目标 `kai/axis-web:evolution-builder-57c33b25`；compose 与原 WEB_SOURCE_REVISION 留有备份，可单独回滚 Web。

线上复核：Web 健康、HTTP 200、WEB_SOURCE_REVISION 为 57c33b2585b38e2a0a8cdc7ea6823f3af5c8ab1c；在已登录 Chrome 中打开实际 17 页 PPTX 产出，侧栏成功排版，后续页面内容可见。

WorkBuddy：保留用户原测试草稿与排除项，追加本次核心回归范围，已用 UI 标注 Free now / 0.00x 的 Deepseek-V4.1-Flash 启动。确认出现运行中停止按钮，已开始 173 HTTP 连通性检查；其测试报告目标为当前项目 `test-artifacts/173-core-regression/`，此时尚未完成完整测试。
