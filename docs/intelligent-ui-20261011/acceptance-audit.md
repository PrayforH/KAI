# A/B 验收审计与待复现样例

本审计的范围是 auto/intelligent-ui-v1 的 A/B review、迭代与 173:3302 部署。原方案 C 为偏好与模型选择，D 为 HTML 扩展。用户已取消新增 UI 专用审批环节；平台既有权限与审批继续生效。

| 要求 | 权威证据 | 状态与边界 |
| --- | --- | --- |
| 目录、校验、组件协议 | presentation/catalog.py、schema.py；核心回归日志 | 已验证协议版本、边界与拒绝降级 |
| 耐久快照、实时与历史一致 | PostgreSQL 五张 UI 表，0039 迁移；173-api-evidence.json | 重读与事件重放匹配同一哈希 |
| 权限隔离、版本检查 | tests/integration/agui/test_ui_actions.py；173-api-evidence.json | 跨用户/租户 404；旧版本 409；权限撤回与恢复测试通过 |
| 幂等动作、排队与恢复 | tests/integration/storage/test_ui_postgres.py；173-api-evidence.json | 本地真实数据库并发及 173 的 12 路请求只有一个子 Run；202 只表示受理 |
| 表单输入与视图恢复 | form-action-completed.png、table-sorted.png、chart-final.png；前端回归 | 173 实际页面核对，覆盖草稿、排序与数据展开 |
| 比较、时间线、指标与流程图 | 173-renderer-before-f.json；补充 renderer 复测报告；前端回归 | 首轮时间线退成列表，流程图走普通 Mermaid，不能计为统一快照通过；G 明确组件复测的两项均发布成功且快照/重放一致，见 173-renderer-explicit-g.json；自然语言自动选型仍单独记录 |
| 数值与渲染边界 | 后端 schema 回归；前端负值图与饼图边界测试；chart-final.png | 已核对给定数据与负值零轴，不宣称所有图形场景正确 |
| 实际发布与迁移 | deployment-evidence.json；部署日志 | 仅演化栈 API/Worker/Web；0039；旧栈和 174 不变 |
| 代码质量 | Ruff、git diff --check、TypeScript；Pyright 基线日志 | 0 个新增 Pyright 错误；保留既有 1522 项 |
| 本地图片获取 | tavily-local-evidence.json | 真实 Tavily 请求及来源；不算 173 图片联网或 HTML 图库通过 |
| 完整自由 HTML 呈现 | rail-file-preview.tsx 现有沙箱 iframe | 现有文件预览已支持；统一自动选择、展示与平台动作链路仍属 D，未计为本轮完成 |
| 微信文章逐项复现 | 用户指定链接 https://mp.weixin.qq.com/s/huFcHuwg6mntXaRQAcZpdg | 用户已提供本地 HTML 与 11 张 GIF，读取资料的阻塞已解除；逐项操作测试尚未完成，见 article-case-matrix.md |

测试数量不是产品体验通过率。原方案中至少 60 个分层样例与人工有用性评测是完整方案的建议发布门槛，尚未将这套语义评测完成计入本轮 A/B 组件和执行链路的证据。用户要求的文章样例复现仍是未完成的交付，文章资料已补齐；浏览器页面状态读取仍超时，继续记录生成探测，交互验收待连接恢复。
