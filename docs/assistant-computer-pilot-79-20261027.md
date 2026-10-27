# 79 本人持久电脑试点：真实模型、多端与重启验收

2026-10-27，北京时间。实现提交 `43f6a83a`，接续[账号与执行授权](assistant-computer-access-20261027.md)。访问地址：[我的助理](https://172.20.105.79:3543/assistant)。

79 已从 0050 升级至 0051，并对本人账号 `xiaokai@shdata.com` 启用持久 Computer。飞书、微信和网页仍使用原个人助理线程及原 Session；没有重绑账号或复制会话。普通任务及未列入试点的账号保留原执行后端。

## 实际环境

- 逻辑电脑：`computer_76649d1de93843289142040974d587f9`。
- 实际容器：`262941df38841f8bbfed06baedde379516c2021781badc642f570a3aa3d198ae`。所有已完成工具轮次都使用它；不是每轮恢复快照生成新电脑。
- 用户工作盘：`kai-computer-76649d1de93843289142040974d587f9`；另有独立私有控制卷。
- 任务用户 UID 28000，HOME `/home/kai`，工作目录 `/home/kai/workspace`。用户脚本和文件持续保留；每轮的执行进程及租约按原监督契约结束。
- 固定电脑镜像 `kai/assistant-computer:20261027-v0h`，digest `sha256:9b926012e692cec7a060124926a2b7638cf81faa13ccd3cb73d72704e11fb88c`。资源上限 2 CPU / 4 GiB，尚无硬磁盘配额。
- 新增内部 `computer-control` 服务，没有发布主机端口。仅该控制服务挂载 Docker socket；用户电脑不挂载它。只有 Worker 持有节点访问私钥；API、飞书和微信仅有一致的试点账号范围，用于维护执行授权版本。

API / Worker / 控制服务使用 `kai/assistant-api:20261027-r20-computer-v0`；飞书与微信分别使用同版本号的 receiver 镜像。Web 维持 `20261026-r16`。五个进程内的 463 个源码及迁移文件逐项哈希均与 `43f6a83a` 对应工作树一致。[镜像](results/assistant-computer-pilot-79-20261027/images.final.json)、[源码比对](results/assistant-computer-pilot-79-20261027/source-verification.json)。

## 真实验收

所有任务经过生产 Redis 队列及真实 Worker、真实模型、真实 PostgreSQL 运行与事件仓库；没有使用确定性 fixture 运行时替代模型。

| 验收 | Run | 结果 |
| --- | --- | --- |
| 第一轮创建文件及 HOME 可执行脚本 | `run_24ddec42b9704f749d20513067baa441` | 实际创建 identity、脚本及首轮报告；UID 28000 |
| 第二轮读取且不重建 | `run_7f88a3abfd4d417ebdcf6e50c019816c` | identity 与脚本输出一致，首轮报告保留；仅发布本轮新报告 |
| 网页刷新后续聊 | `run_52e3453e319049ef8311f8023532322d` | 实际网页输入并发送；回复原代号与 UID，再次刷新仍保留 |
| Worker 重启后网页实际读取 | `run_100324515f1a45948c6fe72372affa1f` | 同一电脑、文件和脚本可继续使用，前两轮报告仍在 |
| 飞书本人消息触发实际电脑读取 | `run_6096615eb19349229c056f51c9199122` | 收件 done，发件 sent，用户身份独立读回机器人回复；网页同一会话可见 |
| 微信本人消息触发实际电脑读取 | `run_a5fbec8984604dcfa6b626acd9d7550c` | 用户实发，真实工具读取，收件 done、发送接口确认，回复原代号；网页历史接口返回同一轮 |

验收代号 `KAI79-PC-20261027-R20-C4N8`。文件为 `computer-pilot/identity.txt`、HOME 下的 `bin/kai79-pilot`、`outputs/computer-pilot-first.txt` 和 `outputs/computer-pilot-second.txt`，保留供继续使用。

Worker 重启前无排队或正在执行的任务。重启后健康，电脑容器保持运行；四个已知试点文件的字节数、SHA-256、mtime 均未改变。重启后的真实任务重新申请执行租约，未误删电脑或工作盘。[重启记录](results/assistant-computer-pilot-79-20261027/worker-restart.json)。

飞书用户身份通过既有 KAI 私聊发送受控测试消息，随后独立调用读取接口确认应用回复；不是把服务器发送成功当成用户端已读。[发送](results/assistant-computer-pilot-79-20261027/feishu-send.json)、[独立读回](results/assistant-computer-pilot-79-20261027/feishu-readback.json)。

微信本人于北京时间 12:28:11 实发消息，本轮真实 Run 成功；12:29:35 收件进入 done，无错误，回复为原代号，微信发送接口确认接收。真实 Bash 工具结果包含两次从文件与脚本取得的同一代号，非聊天记录推断；见[工具记录](results/assistant-computer-pilot-79-20261027/weixin-tools.json)。这不表示手机已读。全部六次真实 Run 使用原 Session，其中五次工具任务的执行租约均释放，电脑保持 idle、epoch 6。飞书七项、微信六项诊断通过。[诊断与实际回复](results/assistant-computer-pilot-79-20261027/channel-verification.json)。

网页已实际刷新并完成续聊，重启后任务及飞书结果也在真实页面可见。最后一条微信轮次通过两次独立、已鉴权的网页历史 HTTP 请求确认：都返回原 Session、同一最新微信 Run、飞书轮次和代号；临时测试登录态已注销。用户当时正在操作 Chrome，未继续抢占浏览器，因此不把这两次 API 读取描述成微信轮次的最终页面渲染验收。[网页历史证据](results/assistant-computer-pilot-79-20261027/web-history.json)。

## 保留、备份与回退

发布前 12 个账号、281 个 Session、809 条 Run 的原 ID 全部保留。飞书配置、绑定记录以及微信账号、绑定代际、bot 标识、启用状态和加密凭据逐项比对不变；新验收只增加运行和事件。验收后共 815 条 Run；新增两张 Computer 表，没有改写原聊天表。[最终记录](results/assistant-computer-pilot-79-20261027/verification.json)。

备份位于 79 `/data/kai-computer-pilot-20261027/private/`：目录 0700，数据库 custom dump 与原 Compose 均 0600。完整备份 130,236,262 字节，备份摘要与原镜像 ID 见[准备记录](results/assistant-computer-pilot-79-20261027/prepared.json)。私钥、原凭据和完整私有配置没有进入代码仓库。

回退应先等待本人电脑任务结束，保留当前工作盘与控制卷并做备份；不能用发布前数据库覆盖新聊天。常规止损可保持当前代码，统一关闭 API、Worker、飞书、微信的 Computer 试点范围后重启四项服务，停止接收新的电脑任务。已有电脑和卷保留，不让 OpenSandbox 回收器接管它们。

如需完整应用回退，在维护窗口确认全部相关执行停止，恢复私有 `compose.before.json` 中 API/Worker r18、飞书 r19、微信 r18 的原镜像及入口；重建对应服务并重新加载 Nginx。保留兼容的 0051 新表，不执行 downgrade，不删除电脑卷。旧临时环境的 Session 快照不包含电脑里新增的文件，恢复旧执行后端之前需先导出所需工作文件；不能宣称两条后端自动共享文件。

79 的授权试验容器、测试数据库和两个 fixture 数据卷已移除，临时测试私钥已删除；本地 PostgreSQL fixture 也已移除。实际本人电脑、工作盘、私有控制卷及生产备份保留。[79 清理](results/assistant-computer-access-20261027/cleanup-79.json)、[本地清理](results/assistant-computer-access-20261027/cleanup-local.json)。

## 当前边界与下一步

本次是持久终端与文件的本人试点。尚未提供桌面、浏览器 profile、登录接管、可视化操作、后台职责编排、原生通道文件投递、闲置休眠或多租户资源准入；不表示 Dot / Grok Bot 的全部 computer 体验已经完成。

实际发现两个待迭代点：首个工具调用仍会串行上传本轮输入与智能体资源，飞书读取的工具等待约 68 秒，需要增加经过远端校验的增量或批量传输，不能只缓存 Worker 内存或跳过权限检查；SDK 显示的本地镜像目录与远端实际 cwd 不一致，需要显式向模型说明真实工具工作目录，同时保留 SDK 自身的本地配置隔离。功能验收成功不代表响应时间已经达标。

实现回归包括 170 项相关测试与 49 项配置/生产装配测试；核心修改 Ruff 与 Pyright 通过，生产装配既有 24 项类型错误与修改前相比无新增。证据沿用[授权迭代](assistant-computer-access-20261027.md)。后续先处理传输耗时和路径说明，再推进 Computer V1 的受认证桌面与互斥接管。
