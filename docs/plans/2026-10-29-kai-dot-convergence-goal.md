# KAI 云电脑收敛与验收至79 — Goal checkpoint

- 建立：2026-10-28 23:18 北京时间。截止：2026-10-29 09:00 Asia/Shanghai；08:30 后不发起大迁移，08:45 收尾整理，09:00 停止新工作。
- 模型：GLM-5.3-Flash（不更换）。token 预算 300,000,000 为上限。
- **原生 Goal 状态：本 ZCode 会话没有 create_goal / 原生 Goal 工具，也没有可设置的 token_budget 字段。**
  如实记录为不可用；不修改内部数据库、不伪称已生效。以本文件 + 会话任务清单承载目标与进度。
- 输入：审查正文 `/Users/xiaokai/.codex/visualizations/2026/10/28/kai-cloud-computer-review/review.xml`；
  探针 `audit-probes.py` / `audit-probes.json`；飞书方案 https://my.feishu.cn/docx/Mp3mdexRAoHGrAxZ5QNcwImmnBb 。
- 实施工作树：`/Users/xiaokai/.codex/worktrees/kai-dot-20261028/agent studio`，分支 `auto/kai-dot-20261028`，
  基线 `77023c5c731dc245c84710864fbf3ce426a56796`（干净）。主工作区 `/Users/xiaokai/Documents/agent studio` 未提交内容不触碰。
- 部署目标：172.20.105.79 本人 canary；必须保留账号绑定、聊天、文件、私有配置、本人 VM 根盘、浏览器 profile 与主题；
  沿用既有受权私有连接，不打印/不扩散密钥。缺认证时记录阻塞并推进独立工作。

## 实施顺序（review §4/§5/§6 + 实施合同）

1. P0-A1 观察态 Viewer：放行只读连接必需的 RFB 协商/像素格式/编码/刷新上行；按协议完整消息边界过滤
   KeyEvent/PointerEvent/ClientCutText 等写入，不按 WebSocket 分片首字节猜类型。viewer.py:235。
2. P0-A2 接管：pending → 拒绝新动作 → 取消排队 → 通知在途输入停止并等排空 ACK → 才发布 human；
   超时保留观察态并给诊断。desktop.py:131 / control_server.py:228。不固定睡 2 秒。
3. P0-A3 动作出队且持锁后复验执行租约、desktop epoch、VM boot 身份、observation 绑定。vm_host.py:506。
4. P0-B 共享 GateAck 校验（version/exit_code/epoch/lease_id/active/boot 身份）：非零、缺字段、错 epoch/lease/active、
   未知状态一律保持 recovering；物理停止必须确认后才可重分配。vm_host.py:345 / docker.py:77（DockerComputerDriver._ack 提取）。
5. P0 反例测试：600/2000 字符在途中接管不再注入；排队旧动作被拒；修饰键释放；旧连接失效；human 失联回落策略；显式交还。
6. P1：observation/frame 与 boot 代际、Viewer 输入代际、结构化 BrowserTools（CDP/DOM 优先、RFB 后备）、
   中文/剪贴板、后台子任务窄授权（不放开全部同账号任务）、Artifact→Outbox 原生文件交付、镜像能力清单按 provider 注入。
7. P2：模板派生、每用户 Viewer、配额、OpenSandbox SDK 薄适配（只在 Provider Adapter 内）、快照契约。
   本人 VM 不因换 SDK 重建。P3 在上述放行后推进。

## 进度日志

- 2026-10-28 23:18 核对基线 77023c5c 干净；建立 Goal checkpoint 与任务清单；P0-A1 开始。
- 2026-10-28 23:2x 原生 Goal 经 /goal 激活；**Budget 显示 Token budget: none / unbounded——300,000,000 上限未被系统接受，如实记录，未伪造**。
- 2026-10-28 23:2x 用户追加：生产 P0 交付截断（run_d805423e 等 3 个 Run "Computer workspace download was truncated"，
  连 6 字节 probe 都失败）+ 助理页产品形态。已纳入清单最前。
- 2026-10-28 23:4x 恢复 79 SSH 通道（沿用既往授权凭据，运行时提取、未打印）。只读取证：
  - 失败 Run 三个：run_8ae2292a (18:14)、run_30acaa26 (23:14)、run_d805423e (23:18 北京)；全在 Run 收尾 collect 的
    workspace.download 截断（worker traceback 指向 workspace.py:204）。
  - 经 VM 路径成功交付过的最大 artifact = 5282 B（13:52 xlsx）；>262KB 的文件从未进入 artifacts 表。
  - guest 挂载 `msize=262144,cache=none`（9P），单次 os.read 上限≈262KB；CHUNK_BYTES=2MiB → 大文件单读必然短读。
  - **根因定性：`workspace_guest.read` 假设 os.read 返回请求字节数（POSIX 允许短读）；host `download` 把短读当致命错误。**
  - probe.txt 失败的机制：publish_artifact 先 sync_workspace（collect 变更集），变更集里的大文件先截断，连带所有 publish 失败。
- 2026-10-29 00:00 `960e21c0` 修复交付截断（guest 循环补齐 + host 容忍短读续读 + 3 条契约测试，31 passed）。
  部署到 79 时 workspace_guest 由 worker 镜像源码下发，**无需重建 VM**。
- 2026-10-29 00:2x 原交付物已在节点做只读保护副本 `/data/kai-dot-20261028/rescue-outputs-20261028/`（哈希与源一致）。
- 2026-10-29 00:35 `c7fdc454` P0-A1 观察态 RFB：RfbClientFilter 按协议完整消息边界分类（协商/取屏全放行，
  键鼠/剪贴板仅 human）；跨接管的半截输入消息→断开连接重连（review §5 语义）。9 测试全过。
  设计权衡记录：首字节判定有流对齐缺陷，完成时判定+翻转断开是唯一同时满足互斥与对齐的方案。
- 2026-10-29 00:45 `859fd0f3` P0-B 共享 GateAck（gate_ack.py）：docker 驱动与 VM reset 共用校验；
  非零/缺字段/错 epoch/错 lease/错 active → 物理停止；停止未确认 → 保持 fenced（recovering）。13 测试全过。
- 2026-10-29 01:30 `9e4f1945` P0-A2/A3 接管协调器：pending→节点排空 ACK→confirm_human（CAS 防陈旧）、
  排空超时回观察态、锁内复验 guard、输入按键间可取消+修饰键释放。343 单测+11 集成过。
- 2026-10-29 01:50 起部署 79（dot13→dot15 / viewer v3→v7）。**两次部署事故如实记录**：
  1. dot13/v3 首轮后发现的 `_busy_event` UnboundLocalError（第二次排空调用崩溃）→ 9e4f1945 修复；
  2. v4/v5/v6/dot14 增量叠加因 tar 解包路径错误全部为旧代码（嵌套 src/harness/src/），修复代码未上线——
     dot15/v7 修正路径并以容器内 grep 验证代码后重roll。
- 2026-10-29 02:1x–03:0x 真实验收 B（viewer）全部通过（GLM 会话内脚本，79 真实 VM/真实 gate）：
  - 观察态冷启动：RFB 完整握手 + 真实帧 1280x800 PNG（/data/kai-dot-20261028/acceptance-20261029/observe-frame.png）。
  - 观察态输入门控：发送 KeyEvent/PointerEvent 后 input_frames 仍为 0/0（键盘鼠标被丢弃）。
  - 接管：takeover_pending → **0.28–0.30s** 内由节点排空确认发布 human（旧实现固定 2s 即宣称）。
  - human 输入：keyboard=1/pointer=1 计入；交还 → observe。
  - 快速重连（模拟刷新）：**5/5 成功**，每次 0.76–1.59s（含完整握手）。
  - 过程中发现并修复两个真实缺陷：viewer 连接清理可被卡死的 drain 阻塞（b2746db4 有界清理+先杀传输）
    与 drain 无界阻塞（016a3667 有界 drain）。多轮"探针失败"经排查全部为探针自身 bug（未缓冲读、双 step、
    0x0 矩形、漏读 name、1GB 读），系统本身健康——如实记录避免误判。
- 2026-10-29 03:15 真实验收 A（交付链路）：真实模型 Run run_7d56400ee4784bc58438a65b7fe4da77 在个人线程
  执行，发布 8 个 artifact：6 字节 probe、中文名 md、HTML、1.2MB PDF、XLSX，以及**原安全护栏报告三件套
  原样取回**（sha256 与工作卷源 fedcdda/542053/8e1ac 完全一致，未重做内容）。全部 8 个 artifact 经真实认证
  API 下载逐一 hash 核对一致。"Computer workspace download was truncated" 生产缺陷（run_d805423e 等 3 个 Run）
  已修复并验证。
- 待办：助理页结果卡（产品项）、P1 系列、08:45 收尾。
