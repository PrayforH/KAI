# 助理加载与编码协同本地电脑：79 验收

2026-11-04。代码基线 ff383602，本次实现至 9d288d03；隔离分支 fix/local-computer-link-20261104，未修改共享脏工作区。用户后续要求提交 develop，合入以最终远端回读为准。

## 改动和实际效果

主助理历史合并先分页再投影子消息，只读取本页结果；终态优先使用版本化 snapshot。条件请求在正文投影前返回，BFF 压缩保留弱 ETag。浏览器不再启动时回放 23 个历史子 Run；合并历史刷新，过期补充卡不再重复读详情。

所有 Bot 共用该历史路径。Bot 聊天入口用 include_duties=false，跳过职责执行历史读取；完整资料接口保持原行为。打开 Bot 时不再恢复主助理的 Agent 绑定，也不允许晚到的目录结果覆盖当前 Bot。普通任务和项目入口保留原分支。

IM 状态在当前聊天历史就绪后挂载。电脑默认关闭，不恢复旧 localStorage 打开偏好；聊天就绪 1 秒后可静默读取状态/入口，不挂载 noVNC、不连桌面 socket、不申请控制租约。实际点击才打开桌面。

仅给编码协同通过现有配置 API 设置 execution_environment=personal_computer（revision 6→7，状态仍 active）。授权使用配置能力而非 Bot 名字；当前跟随该账号已选中的本地电脑。新 Bot 会话和旧职责会话都做同租户/本人/归属/生命周期校验，旧职责须活跃、无项目与外部托管线程，保留原聊天。每次动作继续核对执行租约和 fencing。没有增加排队、优先级、自动抢占、按 Bot 固定目标或工作目录重构。

## 测量口径

同一现场历史数据、隔离只读组合根：原主助理 24 条消息/332 项总记录、303066 字节，1673/1000/1326 ms；新版首个冷样本 636 ms，随后 170/148/207 ms，303186 字节（额外 runStatus 元数据）。弱 ETag 条件请求 61 ms、304、零正文。这是历史 API 时间，不是浏览器完整首屏或模型首 token 时间。

79 实际 API 的 Bot 聊天入口样本：天气 36/28 ms，编码协同 38/29 ms，休息 37/42 ms；各自历史 78/157/50 ms（154280/235554/10994 字节）。旧详情本身已较快（29–51 ms），本轮跳过扫描是减少重复工作和增长成本，不能宣称该接口有数量级加速。

电脑状态发现接口 13–22 ms；私有 RFB 握手 1025–1041 ms。桌面时间与聊天解耦。

## 真实执行及网页

从主会话来源向编码协同投递一条仅执行 pwd、hostname 的验收消息，原会话 assistant_objective_beeac0752f2a4995afa8750a49930427。最终 Run run_97fe61f01dab46c18b974c08c80db828 成功，sandbox.provisioned.provider=remote-computer，工具输出：

```text
/home/kai/workspace
xiaokai
```

主会话持久历史包含对应 sent 和 received 来源行，received 正文为“工作目录是 `/home/kai/workspace`，主机名是 `xiaokai`。”，同一 handover，无新 Bot/职责/定时任务。第一次验收曾仍走 opensandbox-deferred，暴露旧职责会话兼容缺口；修复后再次真实执行成功，不把第一次当通过。

本机 Chrome 独立窗口打开 79：主会话首次电脑关闭，IM 后续显示；点击电脑出现连接状态。新版编码协同页面已看到上述真实结果，电脑关闭；侧栏切到天气、休息，出现对应会话及输入框。保留用户原窗口/草稿，不发送业务消息。原生浏览器工具中途有一次超时，重建后成功读回编码协同页面；没有用 API 健康检查代替这部分实页证据。未使用性能面板测量完整首屏，也未重新做鼠标/键盘与人工接管全套回归。

截图仅保留在本机私有验收目录，不提交用户聊天截图到公开仓库：/Users/xiaokai/.codex/visualizations/2026/11/04/kai-assistant-startup/coding-local-result.png。

## 验证

- 最新 Assistant/API 回归 242 项通过。
- Computer runtime/desktop/VM/control 单元 43 项通过。
- 前端相关 8 文件、43 项通过；TypeScript 无错误，access.py Pyright 0 错误，Ruff 与 diff check 通过。
- 隔离 PostgreSQL 授权套件新增 6 项旧职责归属用例。全套首轮 29 过、1 失败；发现暂停的旧职责无效 payload 被投影成 ValidationError，改为先拒绝非活跃/无效声明，随后对应旧职责及撤销 7 项全部重测通过。其他 23 项未涉及该失败路径；未宣称最后重跑了全套。
- 扩展执行的 assistant-dup-channels 有一条既有源码字符串守卫失败，其断言文本在基线 ff383602 中也不存在；未改动该实现文件。未宣称全仓全套通过。

## 发布

79 API/Worker：kai/superagent-api:20261104-startup-9d288d03，sha256:c9968b90026d8e9d64116d8ee59ab1a92e107015e424c6c643b381676c3e8463。

79 Web：kai/superagent-web:20261104-startup-41a53afb，sha256:519b4c19b9999d858881f78958b6a7c9a8264f7814759f8b47e457f5a73ed462。

79 Control：kai/computer-control:20261104-bot-computer-9d288d03，sha256:e863566ded1092e7939ad135d66cae5703f194425f757c5f4efcc8c3a3464387。

发布目录 /data/kai-assistant-startup-1fdb9abc，最终 release-complete.json。CAS 防止覆盖并发版本，活跃 Run=0 后停 Worker 二次检查，再滚动 4 个服务；均 healthy，nginx 校验及 reload 成功。schema0065 无迁移。Viewer/飞书/微信容器 ID 未变，远端组件 pin 和本地磁盘/浏览器/文件未改；没有操作 174。最终 compose SHA256：3cd2ad1fe97cfe498c407e257ec6194b10d15b20c4d96022206301bae817570b。

## ACP 与后续接入

32.45 /home/xiaokai/acp-test 的 claude-agent-acp 0.100.0、codex-acp 2.5.0 已实测 initialize 成功，协议1。此次没有创建 ACP 会话或发模型任务；原 smoke 客户端会自动 allow_always，未直接复用于 KAI。KAI 任务身份与机主登录身份不同，目前不能声称编码协同已调用 Claude Code/Codex。固定 broker、项目授权、权限卡、取消恢复和真实合成仓库验收见飞书 11.19；他人电脑的当前运维步骤与后续 C1–C4 计划见 11.18。

## 追加：本地 ACP 最小入口已接入（同日）

上文“ACP 未接入”是首次发布时的状态；用户随后授权直接接入。新增 `computer_acp` 通用电脑能力，沿用 Computer 执行租约、归属与动作通道，固定调用两个既有适配器。普通 shell 仍是独立任务账号；ACP 子进程降权为已登记桌面用户 xiaokai，以其 HOME 和固定 PATH 使用既有 CLI 配置，不复制登录文件、不接受任意 user/env/binary。当前仅 Claude plan、Codex read-only，权限请求明确拒绝；目录限制控制会话起点，不宣称是机主 HOME 的操作系统隔离。初版不提供写入授权、持久会话恢复、自动重试或排队。

79 与 32.45 的受信组件一同切换。32 的 root 私有备份位于 `/var/backups/kai-acp-20261104`；新组件 pin 为 `sha256:3434b9f01f414cd2e3c831ba6841828efc12a1fc898915514e6ee5312704a55b`。79 使用 Registry.switch_provider 维护入口切换同一 computer_id 的 image，未写原始 SQL、未迁移生产库，云机目标和磁盘保持原状。发布前活跃 Run=0、执行租约=0，停 Worker 后复查，再安装组件、切换 Registry 和配置，API/Control 就绪后恢复 Worker。Viewer/飞书/微信容器 ID 未变。

首次真实端到端验收 Run `run_75b0c672aa1d4d898e640eeb1a5d4f48` 成功：`sandbox.provisioned=remote-computer`，实际 `computer_acp(agent=claude)` 返回 `ok=true`、`execution_user=xiaokai`、`cwd=/home/xiaokai/workspace/KAI`、`read_only=true`、`ACP_CLAUDE_OK`。工具执行约 11 秒，整个 Run 约 21 秒；仅为这次样本，不是延迟承诺。主助理实页已经看到对应“已发送消息给”和“消息来自 编码协同”及结果，电脑保持关闭。ACP 原始内部 session_id 后续在工具边界剔除，避免成为对话正文。

本机 Claude CLI 复用的是机主已配置的 DeepSeek 网关，不能把这次成功声称为 Anthropic 订阅登录验收。Codex 适配器 initialize/session-new 可用，但完整 prompt 尚未成功；同用户直接 `codex exec --sandbox read-only` 也复现模型元数据读取异常、请求超时重连和 chatgpt.com MCP 请求失败，55 秒有界结束。未修改其全局配置、登录或网络，也未将握手成功当成完整可用。当前演示可使用 Claude，Codex 保留真实失败状态。

追加验证：ACP/电脑套件 70 项通过，新增内部句柄不外泄及相关工具回归后 73 项通过；全前端 1664 项通过、1 项跳过，TypeScript 通过；全仓 Ruff 通过。隔离 PostgreSQL 从空库升级到0065、降级0064再升级0065均通过；修复0044引用最新ORM导致0055/0063重复字段的历史迁移缺陷，79已在0065，不执行历史重放。合入 PR80 仍须满足仓库必需 CI，不绕过保护。

ACP 首次部署目录 `/data/kai-acp-20261104-3c6a175f`，`release.json` 记录四服务健康与镜像摘要；compose SHA256 `f0f08bf66c32dc6648f2f1a5f91ddceb6cd64c6715927100a1e66fe75da0b8e3`。API/Worker SHA256 `6ea7d7ccb40148402d9169869ea11a53a0abd48dad218133dd905407712dda66`，Web `8239f1c0a4803e53c5a1a0ee0aeeadf46163e4bcbf561f2ebf36f7b3b24eb410`，Control `1150075ed0d603e16ad408dd8fb2a0a4c0b513fb88cdb63b2bc1e05347c57579`。随后仅工具输出投影的小切片发布另行记录。

全仓质量门禁补充：本地用相同环境分别检查当前代码与功能基线ff383602，当前3737条、功能基线3826条Pyright诊断；按文件/规则/消息/次数比较，本轮相对功能基线无新增，减少89条。相对于develop保存的旧类型基线仍有2448条未接纳诊断，因此不能声称全仓类型检查或develop合入完成；未扩大baseline或放宽检查。容器安全CI在09238016通过；后端CI已通过空库迁移但停在3条既有lint，当前已修复并本地全仓Ruff通过，最新提交仍须远端复核。

Codex追加定位：桌面GNOME手动代理为127.0.0.1:7897（Clash），后台环境无代理。直连chatgpt.com:443 TCP超时，临时仅测试进程使用既有代理后TLS可达，实际模型请求返回400 model_not_found：“not supported when using Codex with a ChatGPT account”，分别复现于gpt-6.1-sol、model/list列出的gpt-5.5及gpt-5.3-codex。账号查询成功，ChatGPT/Pro，周用量100%；有credits字段，未推断所有额度完全用尽，不披露凭据或余额。现有登录可被复用，但当前Codex账号/模型权限与网络尚未形成可用链路，未改全局网络/模型/登录、未购买额度。

最终受影响回归84项全部通过（370.69秒），包含完整真实PostgreSQL电脑授权套件，旧职责、暂停/撤销、跨用户/租户、项目隔离及最新历史分页/回执/ACP工具。所有测试只用独立容器和15434转发，已清理；不对生产库运行测试建删schema。

最终79 API/Worker更新为 `kai/superagent-api:20261104-acp-2be0744d`，SHA256 `eb347e7ab40c23668f1f724c39bc4ed703447f9d31429788b11caccd9b6cac49`；Web/Control保持首次ACP部署版本。`release-public.json`记录健康，compose最终SHA256 `c6a6005c3e1c84417ebcf99cd5a7afaf9ebd7929eeee44b90a7938e81ca5dd4f`。已部署工具边界会排除session_id。飞书11.20写入revision125，读回确认；11.18保留他人电脑操作步骤和后续计划。代码推至PR80目标develop，受保护分支必需检查尚未全部通过，不宣称已合入。
