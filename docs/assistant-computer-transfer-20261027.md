# Computer 文件准备耗时优化与 79 验收

2026-10-27，北京时间。实现提交 `5f30bbaf`，接续[本人电脑试点](assistant-computer-pilot-79-20261027.md)。已部署到 [79 我的助理](https://172.20.105.79:3543/assistant)，未改变已有账号、绑定和会话。

## 原因与实现

首个电脑工具调用需要先准备本轮输入和智能体资源。原实现每个文件单独经过内部 HTTP、授权、Docker 和监督器执行，约 200 个文件带来约 68 秒等待。工作盘已经持久化，也仍有这部分逐文件传输开销。

`ComputerWorkspace.upload()` 现在将小文件合并为最多 64 个文件、原始内容合计不超过 2 MiB 的批次。更大的文件保留原来的 2 MiB 分块上传；先验证所有路径再发请求。每批仍经同一个 Driver、执行租约、账号与 Run 版本检查，由 UID 28000 的监督器任务执行，不增加 root 文件接口或无授权传输路径。

远端复用原文件实现：目录 fd、拒绝跟随符号链接、校验 SHA-256、临时文件、逐文件原子替换和权限位。批次不是跨文件事务；后续文件失败时，已完成的前序文件保持提交，模型工具不会在准备未全部成功时启动。失败文件的临时上传尽力清理；丢失租约时不绕过边界执行清理。批次类型、声明大小、文件数及起始分块偏移在写入前验证；每个文件继续执行原上传元数据检查。错误校验或中途异常不会覆盖失败目标的原内容。

SDK 的本地控制镜像目录继续留在 Worker。主 Agent 及拥有文件工具的子 Agent 的系统说明现在明确工具实际工作目录，避免将 SDK 展示的 `/tmp/kai-computer-run-*` 当成远端 cwd。路径说明仅在实际远端工具存在时加入，本地运行保留原行为。

本轮没有加入基于 Worker 内存的“文件已存在”缓存，也不以跳过校验来换取速度。后续如需增量上传，应先在远端核实文件内容与权限，再判断可否省略传输。

## 验证

- 120 项相关回归通过，覆盖分块、大文件、空文件、可执行权限、200 个小文件分批、字节限制、符号链接、校验失败和临时文件清理、历史产物不重复发布，以及 SDK、文件代理和工具权限。调整子 Agent fixture 后，主/子远端目录及本地控制镜像用例另复核两项通过，未重复计入总数。
- 本轮修改的核心文件、验证脚本与文件测试 Ruff 通过，Pyright 0 错误、0 警告。未关闭严格检查或忽略类型错误。[测试](results/assistant-computer-transfer-20261027/tests.txt)、[目录用例](results/assistant-computer-transfer-20261027/path-tests.txt)、[类型](results/assistant-computer-transfer-20261027/types.txt)。
- 79 隔离 PostgreSQL 使用真实账号/线程/Run/租约 fixture、canonical 节点授权、真实 Docker Computer 与真实文件系统，运行时为确定性实现，不作为真实模型证据。200 个准备文件全部逐一读取核验；两轮共 9 次批量上传，合计约 5.63 秒。大文件仍用两次分块调用，空文件成功；UID 均为 28000；下一轮保留文件及 HOME 工具，仅发布本轮新报告。[隔离验证](results/assistant-computer-transfer-20261027/worker-probe.json)。

生产真实任务均通过 Redis 队列和新版 Worker：

| 任务 | 实际 Run | 排队至成功 | 首个工具请求至结果 |
| --- | --- | --- | --- |
| 读原文件、运行原脚本、生成新报告 | `run_0473c066d93e44b88fef9bc09f56f533` | 18.003 秒 | 5.124 秒 |
| 飞书实际读取新报告、原文件及脚本 | `run_06ad4c9752ec4e3ba7f5248093d708d4` | 15.016 秒 | 4.943 秒 |

原代号仍为 `KAI79-PC-20261027-R20-C4N8`；实际 cwd `/home/kai/workspace`、HOME `/home/kai`、UID 28000。第一轮仅新增 `outputs/computer-transfer-20261027-first.txt`，第二轮不重写原文件且没有重复发布旧产物。上述时间为本次测量，不是所有任务的延迟承诺。对比 r20 飞书读取的首工具 67.822 秒，这两轮约快 13 倍；整体回复仍包含排队、模型和结果处理耗时。[运行、事件计数与工具耗时](results/assistant-computer-transfer-20261027/verification.json)。

飞书收件 done、发件 sent，用户身份独立读回应用回复，内容确认报告、identity 与脚本一致。[发送](results/assistant-computer-transfer-20261027/feishu-send.json)、[独立读回](results/assistant-computer-transfer-20261027/feishu-readback.json)。

两次独立、已鉴权的网页历史 HTTP 请求均包含新报告和最新飞书 Run，Session 保持原值。飞书七项、微信六项诊断均通过。本次未重复浏览器页面渲染或微信手机实发；微信实际电脑读取和真实网页刷新续聊沿用 r20 试点证据，不能将诊断/API 读取描述为新的手机或浏览器验收。[应用检查](results/assistant-computer-transfer-20261027/application-verification.json)。

## 发布与保留

API / Worker / 内部控制服务：`kai/assistant-api:20261027-r21-computer-transfer`；飞书、微信接收器同为 r21-computer-transfer。电脑镜像保持 v0h，Web 保持 r16。五个服务内的 463 个源码及迁移文件逐项哈希与 `5f30bbaf` 对应工作树一致。[镜像](results/assistant-computer-transfer-20261027/images.json)、[源码核对](results/assistant-computer-transfer-20261027/source-verification.json)。

私有 Compose 仅改变五项镜像引用；全部环境、访问范围、节点私钥、挂载及安全设置保持逐项相等。数据库仍是 0051，无新迁移。电脑 ID `computer_76649d1de93843289142040974d587f9`、实际容器和用户盘均未变化；发布前无活动任务和电脑租约，重启时四个已知原文件的内容哈希、mtime 保持不变；两轮后电脑 idle、epoch 8，租约释放。[发布](results/assistant-computer-transfer-20261027/rollout.json)。

发布前 12 个账号、281 个 Session、815 条 Run 的原 ID 全部保留，验收后 817 条 Run；飞书配置及绑定、微信绑定代际和加密凭据完全不变。

79 发布目录 `/data/kai-computer-transfer-20261027/`，私有备份在 `pilot-private/`：目录 0700、配置与数据库备份 0600。备份大小 130,347,799 字节，摘要见[准备记录](results/assistant-computer-transfer-20261027/release-prepared.json)。回退时先等待电脑任务结束，恢复备份 Compose 的 r20 镜像，重建对应服务并加载 Nginx；保留 0051 数据库、用户电脑、全部用户卷和新增报告，不用旧数据库覆盖新聊天。r20 与 r21 使用同一持久电脑协议及磁盘布局。

隔离试验的客户端、控制器、测试数据库、fixture 工作卷与控制卷已移除，生成的测试凭据已删除；生产私有备份及真实本人电脑保留。[清理](results/assistant-computer-transfer-20261027/cleanup.json)。

## 后续 Computer V1

本轮完成传输耗时与目录说明修正，没有实现桌面、浏览器 profile、登录接管或后台职责编排。下一步仍按[整体方案](plans/2026-10-27-assistant-dot-bot-computer.md)推进：先实现保留工作盘的镜像升级，再提供非 root 浏览器桌面、KAI 鉴权代理与互斥接管，随后进行飞书发起、网页登录、交还控制后继续执行的完整验收。当前本人终端试点不扩展为全用户桌面能力。

后续进展：镜像升级前置已完成代码及 79 隔离验收，见[Computer V1 升级底座](assistant-computer-images-20261027.md)；正式助理尚未切入新迁移，桌面接管继续实施。
