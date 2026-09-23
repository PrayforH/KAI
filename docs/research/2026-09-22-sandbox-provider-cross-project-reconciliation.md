# 三方沙箱 provider 对照：对账与修正（2026-09-22）

对 2026-09-22 那份"我们的 provider vs sapling-deep-agents / deerflow"分析逐条回代码核对。
**结论：三条建议里两条成立但成本比原分析低，一条（"缺会话常暖"）与代码不符；另有四处事实性修正。**

## 0. 版本与来源

| 对象 | 位置 | 版本 |
|---|---|---|
| sapling-deep-agents | `/Users/xiaokai/Documents/agent-studio-model-management/.tmp-sapling-da` | `8b39e56`（squash，仅 1 个提交） |
| deerflow | `/Users/xiaokai/Documents/agent-studio-model-management/.tmp-deerflow-fork` | `9df2b8d` |
| 本仓 | 本工作区，`feature/deepagents-runtime` | 含未提交改动（见 §4） |

remote：`https://git.shdata.com/zhfkt/sapling-deep-agents.git`、`https://git.shdata.com/wangwen/deerflow.git`。
两个克隆在**另一个工程目录**下，不在本仓工作区；如需复现对账要先确认它们还在。
本次只做静态对账，**没有运行**这两个项目。

## 1. 核心修正（按影响排序）

### 修正 1：「L2 会话常暖未接入」不成立——机制已存在，缺的是把生产路线打开

- Daytona：`daytona_session_reuse_enabled` 默认 **`True`**（`src/harness/config.py:147`）；
  `_session_sandboxes: dict[SessionKey, str]` + `_session_locks`（`daytona.py:506-507`）；
  确定性会话名 `harness-session-<sha256(tenant, session)[:24]>`（`daytona.py:509-513`）；
  按 session key 复用（`daytona.py:574-607`）；释放进 warm pool 并 `_reap_warm_entry` / `_evict_excess_warm`
  （`daytona.py:820-889`）。
- E2B 家族（含 CubeSandbox）：`IDLE_DESTROY / IDLE_KEEP_WARM / IDLE_PAUSE` + `harness.keep` 标记
  （`e2b.py:79-84`），`_reuse_warm_sandbox` 按 tenant + `harness.session` + egress 指纹匹配（`e2b.py:620-653`），
  非 destroy 时在 `provision_with_egress` 里复用（`e2b.py:692-697`）。
- 生产路线确实等于关闭，但那是**一行配置**：`cubesandbox_idle_policy` 默认 `destroy`（`config.py:185`），
  而 cube + deferred 是 2026-09-22 起切的生产路线。
- 文档里 L2 的"硬前置"（P1-1 耐久租约）**已经做完**：表 `sandbox_leases`
  （`src/harness/storage/models.py:1202-1206`，migration `0033_sandbox_leases`）；epoch 每次获取递增
  （`lease.py:144`）；`renew(..., epoch=...)` 拒绝陈旧 epoch（`lease.py:169-170`）；`expired()` /
  `mark_reclaimed()` 供回收器使用（`lease.py:194-208`）；Run provision 时 `owner=f"run-fence:{fencing_token}"`
  （`src/harness/worker/orchestrator.py:1096-1105`）。
- 因此 `docs/sandbox-governance-*` 里的"L2 未接入 / 未做"是**产品口径**（尚未在 cube 生产路线启用），
  **不是机制清单**。引用时不要写"我们没有 warm pool"。

**利润要验的点**：deferred 每 Run 仍会 `_ensure_remote` 重新 prepare 并上传本地树
（`deferred.py:128-150`），而每个 backend 仍然给**新的 per-Run 远端目录**
（`daytona.py:639` `remote_workspace = f"{self._remote_workspace_root}/{run.run_id}"`）。
所以常暖省的是**容器创建**，不省上传——真实收益必须实测，不能按"少建一次沙箱"估。

### 修正 2：「deferred 缺文件原语」成立，但成本是接线而不是新建

- 契约层确实没有：`SandboxProvider`（`src/harness/sandbox/base.py:203-219`）只有
  `provision(run)` / `prepare(handle)` / `execute(handle, argv, *, environment, timeout_seconds=30)` /
  `collect(handle)` / `destroy(handle)`；deferred 包装层只碰得到这五个（`deferred.py:106-207`）。
- 但**传输层四个 provider 都已经有 bytes 级 upload/download**：
  `daytona.py:81/85/87`、`e2b.py:154/158`、`kubernetes.py:62/64`、`opensandbox.py:301/304/385`。
- 且传输对象能从 handle 重建：`_remote(sandbox_id)` → `SdkDaytonaRemoteSandbox`（`daytona.py:365/368`）。
- 所以缺的是**把已有传输提升为 provider 原语**（含路径白名单、大小上限、与 collect 的先后语义），
  不是从零做文件面。
- 顺带：未提交改动新增的 `replace_collected_file`（`base.py:173-200`）是为了让 collect 不再原地覆盖只读
  staged input（09-19 的 Errno 13），说明文件面的问题不止 deepagents 那条路。

### 修正 3：09-19「白名单吞掉 DeepAgents 写入」的机制叙述不成立

- 位置修正：`_may_mutate_workspace` 在 **`src/harness/sandbox/deferred.py:172-192`**，不在 `runtime/`。
  现在是**只读黑名单**：`len(argv) > 3 and argv[0] == "python3" and argv[1] == "-c" and
  argv[3] in {"read", "glob", "grep"}` → `False`，否则 `True`（`deferred.py:41-42` 定义常量）。
- 但 `git log -L 172,195:src/harness/sandbox/deferred.py` 显示修前版本（`1f84856f` 引入）第一行就是
  `if argv[0] == "bash": return True` —— **`bash -lc` 形状本来就会 collect**。
  所以"白名单吞掉 DeepAgents 的 bash 写入"这个因果链站不住。
  `docs/deepagents-runtime-design-20260919.md:66-68` 的那段叙述本身有这个缺陷。
- 结论不变（改成黑名单是对的），但**不要把它当作已证实的生产事故机制**引用。
  当前行为有测试钉住：`tests/unit/sandbox/test_deferred.py:159`（只读 → 0 次 collect）、`:188-209`。

### 修正 4：「我们治理面过剩且正确」要按两个默认开关打折

- 三档 enforcement + trust floor 确实有，但**在 `base.py` 不在 `governance.py`**：
  `SandboxEnforcement.FULL/DELEGATED/NONE`（`base.py:21-33`）、每 provider 映射（`36-47`）、
  `provider_meets_enforcement_floor`（`79-89`）、`_TRUST_ENFORCEMENT_FLOOR`
  = `{"safe": NONE, "sensitive": DELEGATED, "untrusted": FULL}` 且未知档位 fail-closed 到 `FULL`（`92-108`）。
  `governance.py` 是实例对账（孤儿/缺失），没有档位。
- `sandbox_trust_floor_mode` 默认 **`report`**（`config.py:40`）——不是 enforce。
- per-Run egress 强制**只有 E2B 实现**（`provision_with_egress`: `e2b.py:682`；`deferred.py:78` 只是转发），
  总开关 `sandbox_egress_enforcement` 默认 **`declared`**（`config.py:34`，注释原文即
  "no backend applies it"）。Kubernetes 是固定的 default-deny + 网关白名单，不是 per-Run 白名单。
- 对 sapling/deerflow 的比较在**机制层**成立；在**生效层**要按这两个默认值打折写。

## 2. sapling-deep-agents：修正后的判定

成立：身份键 = langgraph `thread_id`，无上下文回落字面量 `"default"`
（`utils/opensandbox_backend.py:72`、`utils/thread_util.py:28-35`），即所有无上下文调用共用一个沙箱；
直接调厂商 SDK（`opensandbox 0.1.16`，`pyproject.toml:19`）无自建 provider 层；
`config.toml` 的 `[runtime] type = "docker"`（`docker/opensandbox/config.toml:11-12`，全仓无 gVisor 痕迹）
+ `drop_capabilities` / `no_new_privileges` / `pids_limit`（`:27-30`）+ `[ingress] mode = "direct"`
+ 端口段 40000–60000（`:25-26/:37-38`）；无 trust floor、无 egress 策略（grep 零命中）；
无 lease / 对账（grep `lease|reconcile|ownership` 零命中）；沙箱适配器只有 **165 行**。

需修正：

1. **1800 / 900 的语义**：数值对，含义错。`sandbox_ttl_seconds=1800` 用在
   `SandboxSync.create(timeout=...)`（`config/config.py:118`、`opensandbox_backend.py:83`），
   是**创建请求超时**；`sandbox_renew_seconds=900` 是**续期时长**（`:92`）。
   不存在"池条目 TTL / 续期间隔"，续期**只在取句柄时**发生（`:76`，模块 docstring `:4` 自己写着
   "run 之间零流量"）。
2. **池不是模块级 dict**：声明是实例属性 `self._sandboxes` / `self._lock`（`opensandbox_backend.py:40-41`），
   只因为实例是模块级单例（`:165`）才呈现"进程内池"。
3. **`use_server_proxy=False` 不在 config.toml**：那是 engine 侧设置
   `config/config.py:115 sandbox_use_server_proxy`。`docker/opensandbox/config.toml` 是 opensandbox
   **服务端**的配置（装在另一个镜像里）。两个文件不能混引。
4. **"主动退回 SDK 文件面"只有作者自述**：注释原文实在（`opensandbox_backend.py:137`，含
   "单引号/空格/中文名往返字节一致""不再 mkdir——曾加的 shell 拼接转义不安全且失败被静默忽略"），
   但**没有任何测试**验证往返字节（`test/test_opensandbox_backend.py` 的 fake 只记录调用、返回常量）；
   "退回之前"的证据在**计划文档**（`docs/superpowers/plans/2026-09-15-sandbox-integration.md:468` 的
   `mkdir -p '{parent}'`），因为该 checkout 只有一个 squash 提交，没有可查历史。
   → 只能当"作者自述"，不能当实测结论引用。
5. **镜像耦合更精确**：`config/config.py:117`
   `sandbox_image = "harbor.shdata.com:5000/deerflow/sandbox:1.11.0"`；依赖点在
   `skills/sapling-docx/SKILL.md:7-8`（"docx 已预装在 /mnt/node_modules，不要 npm install"）。
   sapling 自己**不构建**该镜像（它 CI 只烘 opensandbox server 层，
   `.gitlab-ci.yml:61-63`）；deerflow 侧 `shdata/sandbox/README.md:26` 那一行只为**一个 npm 包**
   （docx）背书，不是整套工具面。
6. **别拿 commit subject 当接口说明**：HEAD `8b39e56` 的"工具面镜像四原语两段式"指的是**文档工具面**
   （`upload/artifact-upload/create/update{fileId}`，`documents.py` 与 `templates.py` 两棵树各一份，
   + `move_*_file`），不是沙箱后端；"镜像"一词在代码里根本不存在。
   沙箱后端另有自己的 `execute/upload_files/download_files/id` 四原语（`opensandbox_backend.py:1,107`），
   字面上会混淆。

## 3. deerflow：修正后的判定

成立：两层结构 `sandbox/sandbox_provider.py`（生命周期 `acquire/acquire_async/get/release/reset`）
+ `sandbox/sandbox.py`（文件形状 `execute_command/read_file/download_file/list_dir/write_file/glob/grep/
update_file`）；`acquire(thread_id)`；进程内池 + warm pool + `DEFAULT_IDLE_TIMEOUT = 600` +
`_reconcile_orphans`（`community/aio_sandbox/aio_sandbox_provider.py:134/143/48/233`）；
`fcntl.flock` 跨进程锁（`:24-27/:58`）；per-backend 旗标泄漏
（`uses_thread_data_mounts` / `needs_upload_permission_adjustment`，`sandbox_provider.py:12-13`，
调用方按它分支 `backend/app/gateway/routers/uploads.py:245`）；同步生命周期靠
`_acquire_thread_lock_async` + 专用 executor + 13 处 `asyncio.to_thread` 兜（`:78`、`:51-52`）；
无 per-Run 出口强制（无 `--network` 参数，`local_backend.py:529-551`，且
`--security-opt seccomp=unconfined` `:532-534`；K8s 侧只有 README 待办
`docker/provisioner/README.md:343/357`）。

需修正：

1. **"NOT released" 与代码矛盾**：`middleware.py:34` 的 docstring 写
   "Sandbox is NOT released after each agent call"，但 `after_agent` **确实调 `release()`**
   （`middleware.py:100-105`）——`release()` 的语义是"放进 warm pool，容器继续跑"
   （`aio_sandbox_provider.py:883-892`）。引用要写"**不销毁**"，不能写"不 release"。
2. **跨轮状态不在沙箱里**：`/mnt/user-data`、`/mnt/skills` 是宿主 bind mount
   （`aio_sandbox_provider.py:316-323`），容器销毁与 gateway 重启都还在；丢的只是挂载之外的容器内状态
   （运行时 `pip install`、`/tmp`）。原分析"跨轮状态躺在沙箱里、TTL 回收/宿主重启后语义要靠别处恢复"
   说过头了——他们的模型其实是"**宿主目录是权威、容器是可丢弃外壳**"，这点反而与我们更像。
   且只对 mount 型后端成立：remote/provisioner 后端无挂载，靠 `update_file` 推字节
   （`sandbox/thread_files.py:5-7`）。
3. **"max 3" 是软上限**：满了只记 warning 并超额创建（`aio_sandbox_provider.py:641-643`），
   只驱逐 warm pool 条目（`:832-837`）。
4. **OSError 单一契约只在 `download_file`**（`sandbox/sandbox.py:55-57`，且 `update_file` 无 Raises 子句，
   实现里还抛 `PermissionError`）；而且 `download_file` 全仓**没有生产调用方**（只有定义与 tests）。
5. **镜像**：`1.11.0` 是 **base 镜像 arg**（`shdata/sandbox/Dockerfile:6`），代码默认值是 `:latest`
   （`aio_sandbox_provider.py:45`）；真正装东西的是该仓的定制层（libreoffice/pandoc/poppler +
   `pptxgenjs pdf-lib sharp playwright@1.61.0 @mermaid-js/mermaid-cli docx` + chromium headless-shell），
   冷拉约 10GB（`shdata/sandbox/README.md:98`）；上游 all-in-one 的 Dockerfile **不在仓里**
   （`backend/docs/CONFIGURATION.md:309` 明白写着）。
6. **"单宿主假设"是推断，不是他们承认的边界**：`fcntl` 用法实在，但"只在同一宿主成立"从未被写成限制，
   最接近的措辞是 "(with shared storage)"（`aio_sandbox_provider.py:651`、`sandbox_info.py:15`）。
   他们**自己承认**的是另一件事，原文在 `aio_sandbox_provider.py:240-245`：
   收养无法区分"孤儿"与"别的进程正在用"。引用时不要把两者合成一句。

## 4. 本仓未提交改动（对账时的工作区状态）

`composition.py +174`、`base.py +32`、`daytona.py 14`、`e2b.py 3`、`kubernetes.py 9`、`opensandbox.py 9`，
`src/harness/runtime/` 无改动。`base.py` 的增量是新助手 `replace_collected_file(target, content, *, mode)`
（`base.py:173-200`，临时兄弟文件 + `os.replace`，保留 mode）；四个 provider 的增量是把 collect 侧的写入
统一走它，替换原来的原地 `write_bytes`（Daytona 还去掉了 `chmod(0o600)` 再写的老做法）。
`composition.py` 是另一件较大的事：引入 `InfrastructureAdapters`（`src/harness/infrastructure.py`，未跟踪）
+ `_redis_infrastructure` + 桌面端根 `build_local_container`，把原生产实现抽成 `_build_container`；
其中唯一与沙箱相关的是本地根在启动时拒绝非 `local` provider。**没有 hunk 触及沙箱解析、egress 或 enforcement 逻辑**
——§1 的四条修正不依赖这些改动。

## 5. 修正后的建议（三条）

1. **SDK 当传输、不当 provider——保留，但换之前先算账**。sapling 用官方 SDK 并没有换来我们缺的治理
   （它没有 lease / 对账 / egress / trust floor）。我们 `opensandbox.py` 里那些坑的注释
   （SSE 空行分隔 + 老 `data:` 前缀 `:116-127`、行终止符被丢 `:141-149`、退出码在 `error` 事件 `:152-175`、
   multipart 要 `Content-Length` `:307-313`、argv 被拒要 shell 引号 `:405-409`）**本身就是资产**——
   换 SDK 的判据是"能否在不丢这些处理的前提下减少代码"，而不是"别人用了 SDK"。
   我们目前**没有** `opensandbox` 依赖（`pyproject.toml` 只有 `daytona` / `e2b`）。
2. **给 deferred 补文件原语——成立，且降级为接线**。传输层四个 provider 已有 upload/download，
   transport 可从 `sandbox_id` 重建。真正要定的三件事：路径根必须与策略门同一个值
   （`context.remote_workspace`）、大小上限、以及"写后是否 collect"如何与
   `_may_mutate_workspace` 共存——有了原生写入就**不必再猜命令形状**，这才是这条的最大收益
   （去掉一个已出过事的脆弱推断）。
3. **会话常暖——改成"打开并实测"**。机制与租约都在（Daytona 默认开、E2B 家族可选、
   `sandbox_leases` 已落库），生产路线（cube + deferred）默认 `destroy`。
   要验的是"cube 的 `keep_warm` 在 deferred 包装下确实被复用"，以及上一段算过的账：
   deferred 每 Run 仍重新 prepare + 上传 + 给新的 per-Run 远端目录，**省的是容器创建，不省上传**。

> **后续（2026-09-22，115 实测）**：OpenSandbox 的**平台原生 pool 在 docker 运行时上不可用**
> （`/v1/pools` → 501 `KUBERNETES::POOL_NOT_SUPPORTED`），可用的是 `pause`/`resume` +
> `renew-expiration`（均实测通过）；厂商 SDK `0.1.16` **没有 PTY/WebSocket**，替不掉
> `opensandbox_session.py`。详见 [OpenSandbox@115 实测](opensandbox-115-verification-20260922.md)。
