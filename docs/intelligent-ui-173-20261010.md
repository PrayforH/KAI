# Intelligent UI（harness.ui/v1）第一阶段实现与 173 验证

对应方案文档：[KAI Agent Studio Intelligent UI 架构与实施方案](https://my.feishu.cn/docx/TCHHdVB3Oob3Rtxk3dIcQsa6nVg)（rev 16，2026-10-10）。
本次交付的是方案第 10 节的 **阶段 A：领域协议与耐久呈现**，以及它的 173 验证。

## 分支与提交

- 分支 `auto/intelligent-ui-v1`，基于 `origin/develop`（`942addb1`）。
- 工作树 `/Users/xiaokai/Documents/agent-studio-intelligent-ui`（detached 主工作区有其它会话的在飞改动，未触碰）。
- `9580bb6c` 实现；`af10925f` 按 173 实测的模型输出形状修正校验并做可读性整理。

## 实现范围

协议 `harness.ui/v1` 的字段与限额在 `src/harness/presentation/schema.py` 一处定义；能力目录
`catalog.py` 用同一批条目生成**模型可见的契约文本**（拼进 `VISIBLE_EXECUTION_CONTRACT`），所以
描述与校验不会漂移。身份（`presentation_id`、`revision`、`run_id`、`message_id`、renderer 版本、
策略版本）一律由服务端填充，模型只能提交 `kind`/`data`/`config`/`fallback_text`。

两个生产者在 `projection.py` 汇成**一个纯函数**：

1. **模型自决**：回答末尾的 ```harness-ui 围栏（与已在跑的 `harness-input` 同一机制，不新增工具、
   不动 prompt 之外的东西）。
2. **形状投影**：把已经是表格形状的工具结果（≥5 行、≥2 列、单元格为标量）投影成 `table`，
   `origin=shape` 记录在 `decision` 里。对同类问题已有专门呈现的工具（知识库、memory、控制台自身的
   投影工具）显式排除，避免重复造 UI。

因为实时流和历史重建读的是同一个函数，**同一个 Run 的事件必然得到相同的块和相同的 id**，不需要
另存一份副本，也就不存在"实时有、刷新后消失"的状态。单轮上限 2 块由服务端代码强制。

服务端把每个块发布两次：作为 `harness_present_ui` 域工具调用（控制台渲染的这个通道，历史也用它重建）
和作为 `harness.ui.v1` CUSTOM 事件（其它宿主如 kai-webui 的适配口）。`history.snapshot` 升到 v3 并携带
这些块。新增 `GET /v1/ui/capabilities` 做渲染器集合协商，客户端不能自己扩大授权。

控制台（`web/harness-console`，即 173/174 实际发布的入口）新增
`src/components/intelligent-ui/`：`table`（本地排序、复制、下载 CSV）、`comparison`、`chart`
（bar/line/area/pie 手绘 SVG，附数据表与缺失值说明）、`diagram`（复用既有 Mermaid 渲染器）、
`timeline`、`metric`。块渲染在答案正文之后，和引用 chips、产物行同一位置。块**只有当服务端确实发布过**
时才隐藏原始围栏：被拒绝的块保留为代码块，不留下"内容凭空消失"的空洞。

## 测试

- 后端 `2043 passed, 8 skipped`（`pytest tests/`）。
- 前端 `1062 passed, 1 skipped`（`vitest run`，162 文件），`tsc --noEmit` 与 `ruff` 均干净。
- 新增：`tests/unit/presentation/`（校验边界、投影去噪与确定性）、
  `tests/integration/agui/test_agui_ui_presentation.py`（实时帧 + 历史重建一致、能力接口、
  以及**按 173 实测形状固化**的回归用例）、`web/harness-console/tests/intelligent-ui.spec.tsx`。
- 全量跑时 `tests/integration/storage/test_session_affinity.py` 出现过 1 次失败；单独跑与 3 次随机序
  跑均通过，且与本次改动无关，记为既有 flake。

## 173 部署

沿用该环境既有做法（`/data/agent-studio-evolution-20260920`，compose 项目 `agent-evolution-173`）：
本地出 Next standalone → 打 tar 到 173 → 以**当前运行镜像为基座**做叠加构建 → 换 `compose.json`
→ `docker compose up -d --no-deps --no-build`。切换前先确认非终态 Run 为 0，并留存 `3301/8800`
旧栈的容器 ID 前后对照。

- API/Worker：`kai/axis-api:intelligent-ui-20261010`
- Web：`kai/axis-web:intelligent-ui-20261010`
- 三个应用容器 healthy；`3301/8800` 冻结旧栈容器 ID 未变。

### 两个环境事实（都不是本次改动引入，但会影响后续发布）

1. **173 的 API 基座不是 develop 的源码。** 它带着 `auto/reliable-run-submit` 的
   `worker/dispatcher.py` 与 `storage/execution_commands.py`，而 `harness/core/ports.py` 里并没有
   `RunExecutionCommand`，`worker/main.py` 也不引用 dispatcher——这两个文件在镜像里是**不可导入的死代码**
   （基座镜像单独跑 `import harness.worker.dispatcher` 即报 `ImportError`）。因此本次**没有整包替换
   `harness/`**，只叠加改动的 9 个文件；构建后逐文件对账确认其余 344 个文件与基座**逐字节相同**。
2. **173 的 Web 基座比 develop 旧一个版本**：基座 `/app/node_modules` 里是 Next **16.3.3**，develop 已
   升到 16.3.8（`sharp`/`brace-expansion` override 也各差一档）。第一次部署按 branch 的 lockfile 构建，
   standalone 与基座运行时不匹配，容器起得来但 `/` 返回 500
   （`TypeError: Cannot read properties of undefined (reading 'kind')`）。已**先回滚到原镜像**，改用基座
   自己的 Next 16.3.3 重新构建 standalone 后再次发布，探针容器先验 `/` 200 再切换正式容器。
   结论：这台机器的 Web 反复发布时，要么用基座的 Next 版本构建，要么连 `node_modules` 一起换。

## 真实链路验证与未完成项

在 173 上用 `lead-agent`（`1.0.3+platform.99dfe677`，通用助手）+ 服务 token 发了一次真实 Run，
模型**确实按契约输出了 `harness-ui` 块**，但它用的是 `dimensions: [{"id": ...}]` 加 `values: [...]`
位置数组——与目录里写的 `key` + 对象形态不同，当时的校验直接丢弃了整块。这正是 `af10925f` 的来源：
现在同时接受 `key`/`id`，以及按维度顺序对齐的 `values` 数组，其余仍然拒绝。

**当前 173 上运行的仍是 `9580bb6c`**，也就是**还带着这个形状不兼容**；`af10925f` 只在本地与测试里验证过，
尚未重新部署（API 侧改动 4 个文件、Web 侧 7 个文件 + 1 个测试）。下次发布只需按上面同一配方叠加这 11 个文件。

其余未做：方案里的阶段 B（comparison/timeline/form 的动作执行、审批与排队、revision CAS）、
阶段 C（`topic=ui.presentation` 偏好通道与预算）、阶段 D（HTML 逃生舱与隔离宿主）；
`ui.selected`/`ui.validated`/`ui.fallback` 等 `ui.decision` 类耐久事件、`ui_presentations` 表与
`GET /v1/ui/presentations/{id}` 也尚未落地——本阶段的可重放性由"事件 → 纯函数投影"保证，不依赖新表。
