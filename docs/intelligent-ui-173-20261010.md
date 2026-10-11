# Intelligent UI（harness.ui/v1）第一阶段实现与 173 验证

对应方案文档：[KAI Agent Studio Intelligent UI 架构与实施方案](https://my.feishu.cn/docx/TCHHdVB3Oob3Rtxk3dIcQsa6nVg)（rev 16，2026-10-10）。
本次交付的是方案第 10 节的 **阶段 A：领域协议与耐久呈现**，以及它的 173 验证。
173 的这条栈已按"完全用 develop 分支的版本"重建（见下文"镜像与丢弃的东西"）。

## 分支与提交

- 分支 `auto/intelligent-ui-v1`，基于 `origin/develop`（`942addb1`）。
- 工作树 `/Users/xiaokai/Documents/agent-studio-intelligent-ui`（主工作区是 detached 且有其它会话在飞的改动，未触碰）。
- 提交：`9580bb6c` 实现 → `af10925f` 按现场形状放宽校验并整理 → `70e29382` 本文档 →
  `fe4bcdb0` 再放宽两个现场形状。

## 实现范围

协议 `harness.ui/v1` 的字段与限额只在 `src/harness/presentation/schema.py` 定义一次；`catalog.py`
用同一批条目生成**模型可见的契约文本**并拼进 `VISIBLE_EXECUTION_CONTRACT`，所以描述与校验不会漂移。
身份（`presentation_id`、`revision`、`run_id`、`message_id`、renderer 与策略版本）一律由服务端填充，
模型只能提交 `kind`/`data`/`config`/`fallback_text`。

两个生产者汇成 `projection.py` 里的**一个纯函数** `presentations_for_events(events)`：

1. **模型自决**：回答末尾的 ```harness-ui 围栏（与已在跑的 `harness-input` 同一机制，不新增工具）。
2. **形状投影**：把已经是表格形状的工具结果（≥5 行、≥2 列、单元格为标量）投影成 `table`，
   `decision.origin=shape`。对已有专门呈现的工具（知识库、memory、控制台自身的投影工具）显式排除。

因为实时流和历史重建读同一个函数，**同一批事件必然得到相同的块与相同的 id**，无需另存副本，
也就不存在"实时有、刷新后消失"。单轮上限 2 块由服务端代码强制。

发布通道：每个块同时发 `harness_present_ui` 域工具调用（控制台渲染、历史重建都用它）与
`harness.ui.v1` CUSTOM 事件（kai-webui 等宿主的适配口）；`history.snapshot` 升到 v3 携带这些块；
新增 `GET /v1/ui/capabilities` 做渲染器协商（客户端不能自行扩大授权）。

控制台（`web/harness-console`，173/174 实际发布的入口）新增 `src/components/intelligent-ui/`：
table（本地排序/复制/CSV）、comparison、chart（bar/line/area/pie 手绘 SVG，附数据表与缺失值说明）、
diagram（复用既有 Mermaid 渲染器）、timeline、metric。块渲染在答案正文之后，与引用 chips、产物行同位置；
**只隐藏服务端确实发布过的围栏**——被拒绝的块保留为代码块，不留"内容凭空消失"的空洞。

## 测试

- 后端 `2043 passed, 8 skipped`；前端 `1062 passed, 1 skipped`（162 文件）；`ruff` 与 `tsc --noEmit` 干净。
- 新增 `tests/unit/presentation/`（校验边界、别名归一化、投影去噪与确定性）、
  `tests/integration/agui/test_agui_ui_presentation.py`（实时帧 + 历史重建一致、能力接口、
  以及**两次现场形状**的回归用例）、`web/harness-console/tests/intelligent-ui.spec.tsx`。
- 全量跑时 `tests/integration/storage/test_session_affinity.py` 出现过 1 次失败；单独跑与 3 次随机序跑均通过，
  与本次改动无关，记为既有 flake。

## 镜像与丢弃的东西

用户要求 173 完全按 develop 的版本来，并丢掉没有价值的历史内容。结论与做法：

**丢弃：基座里的外来死代码。** 原基座 `kai/axis-api:develop-voice-32-20261009` 带着
`auto/reliable-run-submit` 的 `worker/dispatcher.py` 与 `storage/execution_commands.py`，但
`harness/core/ports.py` 里没有 `RunExecutionCommand`、`worker/main.py` 也不引用 dispatcher ——
这两个文件在镜像里**不可导入**（在基座镜像里 `import harness.worker.dispatcher` 直接 `ImportError`），
既不参与运行也没有被任何入口调用。**它们在机器上的价值为零**，已随整包替换消失
（新镜像核对：`harness` 347 个文件、`stray dispatcher: absent`）。
该**分支本身**仍有设计价值（Run/事件/执行意图同事务、`run_execution_commands` 为唯一权威、Dispatcher 租约投递，
带测试），因此只删镜像里的残留、不删分支；教训是**别再把它当局部 overlay 叠进镜像**——那种"只复制某分支的部分文件"
的做法会造出既不属于 A 也不属于 B 的镜像，这正是这次的来源。

**重建后的 173 演化栈**（`/data/agent-studio-evolution-20260920`，compose 项目 `agent-evolution-173`）：

| 组件 | 镜像 | 来源 |
| --- | --- | --- |
| API + Worker | `kai/axis-api:intelligent-ui-20261011` | 基座 + 本分支**整包** `harness`（347 文件）+ apt 安全升级 + 四个 Python 安全下限 |
| Web | `kai/axis-web:intelligent-ui-20261010-clean` | `deploy/docker/web.Dockerfile` 本机构建后导入，Next **16.3.8**，自包含 |

三个容器 healthy，`web /` 200、`api /healthz` 200；`3301/8800` 冻结旧栈（`deepagents-20260919.9`
/ `deepagents-20260919-web`，起于 2026-09-19）**容器未重建**。

### 为什么不是"从零构建整个 api 镜像"，以及代价是什么

`deploy/docker/api.Dockerfile` 在 173 上无法构建，证据是逐项实测的出口能力：

| 目标 | 173 主机 | 构建容器内 |
| --- | --- | --- |
| `pypi.tuna.tsinghua.edu.cn` | 可达 | 可达（pip 成功） |
| `deb.debian.org` | 可达 | 可达（apt 成功） |
| `dl-cdn.alpinelinux.org` | 可达 | 不可达（`apk upgrade` 失败） |
| `registry.npmmirror.com` | 可达 | **IPv6 优先解析、容器无 IPv6 路由** → 下载与 `npm ci` 失败 |
| `github.com` / `proxy.golang.org` | 不可达 | 不可达 → **kubectl 无法重建** |
| Docker Hub / `cgr.dev` | 不可达 | 不可达 |

所以 API 走仓库自己的增量路径 `deploy/docker/api-update.Dockerfile` 的 173 适配版
（`ui-build/deploy/docker/api-173.Dockerfile`）：apt 安全升级 + `claude-agent-sdk==0.2.152`
与 `pypdf>=6.17`/`PyJWT>=2.15.1`/`anyio>=4.14.2`/`urllib3>=2.8.0` 下限 + 整包 `harness`。
实测下限已生效：`pypdf=6.20.0 PyJWT=2.15.1 anyio=4.15.1 urllib3=2.8.0`。

**仍未复现的 258d6c4e 残余**（都属安全/打包层，非功能缺失）：Node 22.9.0→22.23.3 与 office npm 的
锁定重装（基座已带 `docx`+`pptxgenjs`，能力不变；容器内 `npm ci` 受上述 IPv6 限制）、
kubectl 上游基础镜像的 digest 更新（重建出的二进制相同）。要做完整从零构建，需要一台同时能访问
Docker Hub、github/Go 代理、apt 与 npm 的机器，再推 Harbor 供 173 拉取。

Web 侧另一个坑：Harbor 上的 `dependencies-ai/node:22-alpine` 是 **arm64**，在 173 上构建直接
`exec /bin/sh: exec format error`；因此 web 镜像在本机用仓库自己的 Dockerfile 构建（`--platform linux/amd64`，
JS 阶段跑 BUILDPLATFORM 不做 QEMU），分片传输后 `docker load`，`sha256` 与本地一致。

## 真实链路验证

在 173 上用 `lead-agent`（`1.0.3+platform.99dfe677`）+ 服务 token 发真实 Run。**两次现场形状都被抓到并修掉**：

1. 第一次：`dimensions:[{"id": ...}]` + `values:[...]` 位置数组（`af10925f`）→ 现在接受。
2. 第二次：`dimensions:["成本","周期","风险"]` 裸字符串 + items 用 `key` 命名（`fe4bcdb0`）→ 现在接受。

最终一次真实 Run 的结果：

```
answer has fence: True
published blocks: [('comparison', 'model', 'ui_203eb729a289bbdbfc5a62cb')]
  title: 方案A/B/C 评估对比
  dimensions: [('成本','成本'), ('周期','周期'), ('风险','风险')]
  items: ['方案A', '方案B', '方案C']
history blocks: ['ui_203eb729a289bbdbfc5a62cb']
LIVE == RELOAD: True
```

另外确认线上控制台的分片里确实带有渲染代码（`harness_present_ui` 命中 3 个文件、intelligent-ui CSS 在
`static/chunks` 内）。**未做**：浏览器内的视觉验收（需要登录态），因此"块长什么样"目前只有单元测试与
构建产物证据，没有截图。

另外两点实测行为值得记录：模型在**没有真实数据**时会拒绝编造，并明确说明"没拿到字段结构定义"——
这提示契约文本的可见性依赖模型是否读到系统提示尾部的这段常量，多 provider 路由下不可假设。
所以校验侧的宽松归一化不是可选项，而是主通道可靠性的组成部分。

## 阶段 B（第一片）：表单与块上的动作

这一片把"业务交互"里最内层的东西做出来了：**用户点一下块上的按钮，或填完一张表单，就是他的下一轮发言**。
动作自己贡献一句指令，用户贡献选择或填写的值，两者合成一条普通用户消息——平台因此只看到它本来就会
管的那条消息，没有新增通道、也没有新增信任面（策略、审批、排队全部沿用既有 Run 链路）。

- 新组件 `form`：字段 type 取 text/number/date/single/multi，单/多选必须给 2–8 个选项；
  `submit_label` 命名真实操作。
- `actions`：每个块最多 4 个，kind 取 `conversation` / `read` / `write`；
  **`action_id` 由平台分配**（`<presentation_id>-a<序号>`），模型只声明按钮文案与它要说什么。
- **表单必须声明一个 `read` 或 `write` 动作**，没有去处直接判无效，而不是渲染成一个走到死路的界面。
- `local`（排序、切序列这类）**故意不做成可声明的动作**：那是组件自己的本事，不该往返一趟。
- 没有回合上下文时（例如预览）表单只读展示字段、不给按钮——答案没有地方可去。

验证：python `2056 passed / 8 skipped`、前端 `1067 passed / 1 skipped`、ruff 与 tsc 干净。
173 上换 `kai/axis-api:intelligent-ui-20261011b` + `kai/axis-web:intelligent-ui-20261011`
（web 容器内 `BUILD_ID=wnbC-knn2ZOUq_WWQex25`，分片里 `form` 渲染器 4 处、动作条 4 处），三容器 healthy。

真实 Run（同一 `lead-agent`）要求"补一张采购立项表单"：

```
published: [('form', 'ui_4bc12cd60dadc7e570b6fa03')]
  fields: [('project_name','text',False,[]), ('applicant','text',False,[]),
           ('priority','single',False,['高','中','低'])]
  submit_label: 发起采购立项
  actions: [('ui_4bc12cd60dadc7e570b6fa03-a0', 'write', '提交立项申请', 'primary')]
LIVE == RELOAD: True
```

一个诚实的观察：这次模型把三个字段的 `required` 都填成 `false`，尽管它在同一段话里说这三项是缺的。
契约目前没有把 `required` 的语义讲透，下一轮迭代要在契约文本里明确"用户必须补的字段要标 required"。

**这一片没做的（阶段 B 的其余部分）**：独立的动作端点 `POST /v1/ui/presentations/{id}/actions` 与
服务端幂等键、`expected_revision` 的比较交换、`ui_presentations` / `ui_action_requests` 表、
以及把 `action_id` 映射到"服务器允许的工具 + 参数 schema"的动作注册表。目前 `read`/`write` 与
`conversation` 一样走普通回合，所以幂等、审批、排队用的是既有 Run 语义——够用，但还没到方案里
"重复提交必须返回同一 Run"那种由动作层自己担保的强度。

## 未做

方案里的阶段 B（comparison/timeline/form 的动作执行、审批与排队、revision CAS）、阶段 C
（`topic=ui.presentation` 偏好通道与预算）、阶段 D（HTML 逃生舱与隔离宿主）；
`ui.selected`/`ui.validated`/`ui.fallback` 等耐久事件与 `ui_presentations` 表 +
`GET /v1/ui/presentations/{id}` 也尚未落地——本阶段的可重放性由"事件 → 纯函数投影"保证，不依赖新表。
