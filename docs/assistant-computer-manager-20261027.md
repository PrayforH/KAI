# Computer V0：Manager、执行隔离与 79 组件验收

2026-10-27，北京时间。接续[注册与工作盘迭代](assistant-computer-v0-20261027.md)。本轮已在 79 实际内核上验证受控执行、文件延续和失联恢复，但尚未接入个人助理的真实模型 Run、Worker、桌面或接管界面。生产仍为 0050，现有聊天继续使用原配置。

后续进展：同日进一步接通 Worker 作用域、长期工作盘与产物基线，并通过隔离 Docker 验收；见 [Worker 桥接记录](assistant-computer-worker-20261027.md)。生产路由和业务授权仍待接入。

## 本轮实现

`ComputerManager` 把数据库租约与物理操作连接起来：启动前保留电脑和工作盘身份，创建确认先入库，再初始化。明确拒绝创建时释放失败租约；超时、断线或初始化结果不确定时保留租约，之后通过恢复确认资源。运行期间同时维护数据库与物理权限；心跳失败会取消当前工作。只有驱动确认执行进程退出后，才释放数据库执行权。

Docker 首版使用稳定容器名称和独立工作盘，恢复可替换实例但保留电脑身份与原工作盘。`volume_ready` 记录磁盘是否曾初始化；已经使用过的盘丢失时拒绝创建空盘冒充恢复。容器创建、发现和删除由 PostgreSQL advisory lock 串行协调，取得锁后重新校验租约，不在网络调用期间锁住电脑行，避免阻塞心跳。删除使用检查后的不可变 Docker ID。

本轮采用独立 Docker 驱动，普通临时任务保留原 OpenSandbox 路径。原因是持久电脑需要在任务权限到期后停止旧写入者；不能把 root execd 暴露给用户命令，再仅依赖数据库 token 隔离执行。未来可以适配其他运行时，但必须保留同等的物理执行契约。

实现入口：

- `computer/manager.py`：启动、执行、恢复及心跳协调。
- `computer/docker.py`、`host.py`：驱动、归属检查、固定镜像和命名盘生命周期。
- `computer/control.py`、`control_server.py`：独立内部控制服务，私钥鉴权后才读取命令体；校验数据库当前持有者，覆盖请求自带的 epoch 与有效期。只提供受控生命周期和监督器动作，没有通用 root shell 接口。
- `computer/supervisor.py`：固定 root 监督器；用户命令经一次性私有 spool 传入，实际使用 UID/GID 28000 与 no-new-privileges 执行。工作电脑不挂载 Docker socket，也不启动 execd。
- `deploy/assistant-79/computer.Dockerfile`：固定监督器、执行用户与目录。79 测试镜像为 `kai/assistant-computer:20261027-v0h`，驱动测试配置固定到不可变 image ID。

控制服务具有宿主 Docker 权限，必须作为受信任的内部节点服务运行，密钥仅供经过授权的 Worker 使用。工作电脑的只读根目录、工作盘和私有控制盘是分开的；控制盘 root-only，用户任务不能读取。一次性参数不会拼到 root 命令行或错误响应中。当前代码没有新增公开 KAI API。

## 进程与旧内核兼容

每个执行租约创建独立 PID / mount namespace，由 root namespace init 监督到期状态；用户命令在其中以普通用户运行。关闭或到期时退出该 init，内核回收整个 PID namespace 中的进程，覆盖脱离进程组的守护进程和主线程退出后仍存活的线程。单纯杀进程组不能提供这一保证。[Linux PID namespace](https://man7.org/linux/man-pages/man7/pid_namespaces.7.html)

79 为 `3.10.0-1160.119.1.el7.x86_64`，没有使用新内核 pidfd 作为依赖。监督器核对 PID 起始时间及 namespace inode；退出瞬间的观察错误会重试，无法确认退出时拒绝交接权限。控制用的能力只在固定 root 监督器中保留，任务降权后不能重新获得。这里验证的是应用执行边界，容器共享宿主内核，不代表 VM 级隔离。

## 实际验收及证据

所有测试使用随机 fixture 租户与独立 `harness_computer_probe` 数据库。没有使用本人聊天身份或工作文件。

| 验证 | 实际结果 |
| --- | --- |
| 79 第一次受控执行 | 真实/有效/保存 UID 均为 28000；控制状态不可读、Docker socket 不存在、execd 端口不可达 |
| 两轮执行 | 新建 HTTP 客户端及 Manager 后仍连接同一实际容器和工作盘，读回上一轮文件及用户目录可执行工具 |
| 执行结束 | 脱离原进程组的后台写入者停止，后续执行才开始；旧租约被拒绝 |
| 模拟失联 | 不运行 Manager 心跳及正常收尾；到期 watchdog 在恢复前已经停止后台写入者 |
| 恢复 | 删除旧实例并确认不存在，创建新实例，保留同一电脑身份、工作盘、文件及用户工具 |
| 79 Linux 测试 | 9 项通过，覆盖权限、旧 epoch、孤儿进程、残留线程、到期、超时、namespace 丢失及一次性参数清理 |
| 本地回归 | 45 项通过，覆盖真实 PostgreSQL 注册/租约/Manager/宿主协调和既有沙箱租约治理、内部 API 鉴权 |
| 静态检查 | Ruff 通过；显式选择项目 Python 环境的 Pyright 0 错误、0 警告 |
| 迁移 | 空库 0001→0051、0051→0050→0051、旧用户记录保留，以及两张 Computer 表与 ORM 元数据一致 |

迁移复核发现 `0001` 按动态 ORM 元数据初始化，会把后来导入的 Computer 表提前创建，导致 0051 重复建表。本轮限定初始建表集合，Computer 两表仍由 0051 创建；上述空库与回退检查验证了修复。0051 尚未在生产部署，因此可继续完善未上线的迁移。

证据：[Manager 实测](results/assistant-computer-manager-20261027/manager-probe-79.json)、[79 Linux 测试](results/assistant-computer-manager-20261027/supervisor-tests-79.txt)、[45 项回归](results/assistant-computer-manager-20261027/focused-tests.txt)、[迁移](results/assistant-computer-manager-20261027/migration.json)、[镜像与源码指纹](results/assistant-computer-manager-20261027/environment.json)。测试脚本为 `scripts/probe_assistant_computer.py`，强制要求独立数据库名称，不能指向生产数据库运行。

组件试验结束后删除测试容器、fixture 工作盘和控制盘、API 中的临时探针目录及生成的测试密钥。测试镜像与无密钥报告保留。[79 清理](results/assistant-computer-manager-20261027/cleanup-79.json)、[本地清理](results/assistant-computer-manager-20261027/cleanup-local.json)。生产账号、原 Session/Run ID、飞书配置和绑定、微信绑定代际及加密凭据逐项核对保留，服务镜像未变。[生产核对](results/assistant-computer-manager-20261027/production-verification.json)

## 下一轮接入及未完成边界

1. 从已授权的真实个人助理 Session 推导电脑归属，接入 Worker / SandboxProvider；控制节点额外检查账号、Run 和 Session 的当前授权与状态。当前内部租约验证不能替代完整业务授权。
2. 以远端工作盘为长期文件事实来源；跳过旧 Session 快照覆盖，仅上传本轮新附件和必要运行文件。取得电脑后记录基线，只收集本轮新增或修改的文件，避免每轮重复发布历史文件。
3. 区分临时沙箱与电脑实例的清理归属，处理 Worker 重启、取消、节点失联和实例治理；还需配置节点归属、网络访问策略、资源准入与真正的磁盘配额。Docker 命名盘没有自动获得容量硬限制。
4. 在 79 本人账号执行两次真实助理任务，验证文件、工具延续，以及停止、Worker 重启、网页刷新续聊；飞书、微信继续使用原会话。当前受控组件命令不能算作完成这些验收。
5. 再接浏览器桌面、登录 profile/keyring、认证 HTTP/WebSocket 代理、观察与人工接管互斥。初始化 profile 目录不等于真实浏览器登录持久化，本轮也未保存运行进程内存。

完成以上接入前，不将持久 Computer 标为线上已启用，也不增加会误导用户的“电脑已连接”状态。
