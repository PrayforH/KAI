# 工作区传输：从逐文件往返改为一次归档 + 批量文件面（2026-09-22）

分支 `fix/sandbox-workspace-transfer`（基于 `origin/develop`）。这是"给 deferred 补文件原语 / 归档面"
两条建议的落地记录，含真机实测数字。

## 1. 改了什么

**归档面（回收方向）**：`e2b.py`（含 CubeSandbox）与 `opensandbox.py` 的 `collect` 原本是
`list_files` 之后**逐文件一次数据面请求**。现在改为沙箱内 `tar -C <workspace> -cf <tmp> .`
后用一次流式读取取回，本地用共用解包器展开。

- 解包器与它的成员安全规则（绝对路径、向上穿越、非常规文件）抽到
  `sandbox/base.py:extract_workspace_archive`，之前 **daytona 与 kubernetes 各有一份近似拷贝**，
  本次一起收敛到一处（`workspace_archive_transfer_limit` 同理）。
- **保留逐文件回退**：镜像里没有 `tar` 时抛 `WorkspaceArchiveUnavailableError`，`collect` 记
  warning 后走老路。回收发生在答案已经落库之后，回退慢但不该让一个成功的 Run 变失败。
  而尺寸/安全类错误**不回退**，直接失败——那是越界，不是慢。

**文件面（写进去方向）**：`prepare` 改为批量写入（E2B 走 `files.write_files`，32 文件 / 8 MiB 一批；
OpenSandbox 原本就是批量）。

**文件面成为一等能力**：能绕开命令面的后端实现 `upload_files` / `download_file`；deferred 包装层转发
并**由文件面写入直接把工作区标脏**（不再依赖命令形状推断），egress 包装层也转发该能力（否则能力被遮住、
调用方探测不到）；DeepAgents 后端优先使用它，没有该能力的后端行为不变。

## 2. 真机实测

**CubeSandbox@111（本分支已含 cube v1 创建修复）**

| 文件数 | 改动前 | 改动后 |
|---|---|---|
| 800 | 1.18s **失败**，openresty 502，只取回 **249/800**（2026-09-21 在 173 记录） | **0.72s，800/800 完整** |
| 2000 | 未测 | **1.09s，2000/2000 完整** |
| 4500 | 连 fill 都 502（2026-09-21） | 仍失败，但**不在回收**：填充命令本身间歇性被掐，一次运行的归档读取也收到 502 |

4500 的失败是**平台数据面本身撑不住**，属于已押后的 CubeSandbox 侧问题；本次改动把失败点从回收移走了，
但没能（也不该由客户端）解决那个。

**OpenSandbox@115（多文件写入的对照，同一次运行内）**

| 路径 | 200 文件 | 每文件 | 请求数 |
|---|---|---|---|
| 命令代理（`python3 -c` + base64 走 argv，即 DeepAgents 原路径） | **241.02s** | 1.205s | 200+ |
| 原生批量文件面 | **0.53s** | 0.0026s | **7** |

回收侧：400 文件一次归档 **3.43s**，本地 400/400。

## 3. 测试与门禁

- `tests/unit` 全量 **1465 passed**；改动后又跑 `tests/unit/sandbox tests/unit/runtime` **547 passed**。
- 新增用例：共用解包器的越界/尺寸/成员规则（经 provider 的 collect 覆盖）、归档路径取一次归档
  而非逐文件、tar 不可用时的回退、归档越界**不回退**、`prepare` 批量写、文件面脏标记与只读不脏、
  命令面后端拒绝文件面、DeepAgents 后端优先文件面（含按文件报错）。
- **pyright 门禁与基线持平**：父提交 1088 → 本分支 1088（零新增）。第一次提交引入了 7 条
  （协议声明了 SDK 不支持的 `mode` 参数、新用例缺 `reportArgumentType` 忽略），已修。

## 4. 未覆盖

- **4500 文件级**：见 §2，平台侧问题，未解决。
- **Daytona / Kubernetes 没有逐文件文件面**：它们的传输只有归档（`download_archive`/`upload_archive`），
  所以文件面能力只加在 e2b/cube 与 opensandbox 上；其余后端保持命令代理，行为不变。
- **本分支不含 `41772bc0`**（E2B collect 显式 request deadline）：它在 `auto/agent-evolution` 上。
  归档路径本身就消除了逐文件循环，但两条线合并时要注意这处重叠。
- 未在 173 演化栈上部署验证；未推送、未开 MR。
