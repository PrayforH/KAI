# 工作检查点

工作树 /Users/xiaokai/.codex/worktrees/kai-remote-computer/agent studio。分支feat/remote-computer-20261103。ZCode监督仍暂停。不要操作174和共享main脏工作区。

代码、服务已实现并测试，当前正发布79。SSH master79=/tmp/kai-remote-release.sock（root）；本机45=/tmp/kai-remote-computer-32-45.sock（xiaokai）。登录凭证由用户当前聊天授权，不写文件。若master失效按聊天身份重新登录。

私有注册材料 /tmp/kai-remote-enrollment（不要输出/提交密钥）；79发布目录/data/kai-remote-20261103；release.py做CAS，备份compose.before.json，检查无活跃Run再替换api/worker/web/control/viewer。反向Unix tunnel已建立，32的kai-remote-link、kai-remote-display服务活跃。桌面使用现有:0，无5900 TCP监听。曾x11vnc默认额外监听IPv6，已立刻停止并修正为-rfbportv6 0 -noipv6，当前无TCP桌面暴露。

最终远端组件pin sha256:dac63fd8174ce21e6007c6e70d01973a4e8bf060e0cd9278b9273239c43c35dc，已root安装。身份用machine-id与组件实际hash双核对。控制及viewer私钥仅79private，tunnel私钥仅32独立link账号。云虚机与原磁盘未改。

构建：api fce866fa3ccab511ed97a7276b881f057ebb904eddfbb8db7b502498ede63d12；viewer e6de982f1f50adb861b256d18681b03e962e35868f720ae1e814b039ca1d8f4e；web b39ed08ced64413270f40d1f44709a08c95ab8098419eb296a05376df7ed1d7f。tag均20261103-remote-computer-v1。web9层，api基于6b9单COPY层。

日志 /tmp/kai-remote-unit-tests.log 177passed；/tmp/kai-remote-db-tests-final.log 25passed；/tmp/kai-remote-web-tests2.log8passed；/tmp/kai-remote-tsc-final.log空；/tmp/kai-remote-pyright3.log0errors。旧VM测试fixture返回空ack已改为真正epoch/lease/active，不削弱生产验证。隔离DB kai_remote_computer_1103，fixture会DROP schema，只能指定该库。

CUA：extension创建页超时kernel reset，改native remoteChrome = await cua.getApp('Google Chrome')。已自动创建一个专用group“🖥️ Remote Computer 验收”，含我的电脑页。上次AX212为该tab，要fresh获取索引。当前主页面空输入，DeepSeek Flash（验收若临时用MiniMax最后恢复）。不要重复创建或覆盖用户draft。UI动作只能CUA，真实桌面通过79的noVNC或主模型computer_*触发，不能shell/pyautogui代替实际UI验收。

待办：确认发布health/active模块hash；CUA打开专用电脑页，选本地，截图；创建loopback合成验收HTML（不含私密数据），真实模型GUI打开并输入点击；CUA人工接管/输入/拒绝模型在human时动作/交还再模型续操作；切回云再本地均实屏验证；收尾无活跃QA，保留可切换电脑，同步飞书11.14并回读；报告/截图/代码commit。飞书文档XV4ndItNmoONoIxQPWgcIyLbnJe，目前revision102，lark-doc/shared及refs已读。

## 用户追加升级32.45到24.04（已明确授权）

用户再次澄清只升级192.168.32.45，并于当前聊天明确回答“已保存，允许升级和重启32.45”。不要再次索要同一授权。Mac和79操作系统不升级。升级后实测agent-desktop，视其实际能力接入辅助功能树（原任务仍继续）。

32.45根分区ext4无LVM/Btrfs快照，159GB空闲，RTX3090/NVIDIA。官方do-release-upgrade -c返回24.04.5 LTS。Prompt=lts、dpkg audit干净、无hold。已创建root-only配置/boot/dpkg备份、包/服务/Docker状态清单，位置/var/backups/kai-ubuntu-2404-20261103；这不是完整根盘镜像，不虚假声称一键回滚。完整HOME/Docker数据不做破坏性重建。

当前root systemd oneshot `kai-ubuntu-preupgrade`正执行22.04最新更新（apt dist-upgrade Always-Include-Phased-Updates, keep configs）、随后安装update-manager-core和screen。脚本/tmp/kai-prepare-upgrade.sh，journalctl -u kai-ubuntu-preupgrade可读窄日志。SSH master仍在；若升级/重启断开重新登录指定IP。需要基础更新完成后检查是否需先重启，再执行官方do-release-upgrade，必须停在24.04不-d不26。用tmux/screen持久运行并监控交互，保留现有SSH/Docker配置，确认升级计划/移除包范围，不盲目全yes。最终允许重启已获授权。Ubuntu官方参考https://ubuntu.com/server/docs/how-to/software/upgrade-your-release/已浏览。

79已经部署v1，5组件healthy。COMPOSE SHA f0ff511eee20262c9f6c80db23678eaf0180da95ef70d7c52b2d2e570dec04f9。发布后reload了nginx（配置测试通过）以刷新container DNS。公开targets接口在真实浏览器查看返回cloud/local-32-45两个目标；云为active。当前未做实际切换。本机正升级期间保持云active。

浏览器native操作遇到用户介入/旧页面加载问题，不能称GUI验收完成。专用group仍有电脑页，另开了一个/api/studio/.../targets只读JSON页（可关闭/复用为QA主会话）。remoteChrome绑定当前Chrome；操作前新AX。最近电脑页导航about:blank后尝试重新打开pending，AX有时about:blank＋地址栏电脑URL，执行Return似乎焦点未生效。遇到user changed提示必须重新getAX，不与用户抢浏览器。API实际返回、生产包内新选择器代码已确认，均不等同实页完成。

19:44基础更新完成PREUPGRADE_COMPLETE，systemd result success，SecureBoot disabled。/run/reboot-required存在。正在执行已授权的第一次32.45重启，再跑24.04发行版升级。tmux已安装、screen已安装。NVIDIA由550过渡到580 Ubuntu包；重启后验证nvidia-smi。32.45不开自动登录，GDM重启后可能需通过受控私有桌面完成原用户登录，不能擅自打开自动登录或丢弃原用户桌面。

浏览器卡住原因已找到：createBrowserTab失败遗留了“ChatGPT调试”连接，页面在导航后不执行JS。用nativeChrome点击自己验收tab的调试banner取消后，新“选择电脑”控件立即出现，默认云电脑。以后本任务只用native CUA，避免重新使用已失效extension。remoteChrome持久绑定，取消调试不影响native。截图尚未保存。
