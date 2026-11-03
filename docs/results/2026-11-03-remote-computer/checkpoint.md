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

19:48第一次重启后SSH恢复，Ubuntu仍22.04（前置更新阶段），RTX3090 driver580.178.04正常，SSH/Docker/link/display均active，用户seat0会话已登录。新SSH master=/tmp/kai-remote-upgrade-32-45.sock，ControlPersist7200。

发行版升级在root tmux会话kai-ubuntu-2404中运行，执行PTY session_id=22585。终端TERM必须xterm-256color；默认dumb会不能clear。实际启动命令sudo env TERM=xterm-256color tmux new-session -A -s kai-ubuntu-2404 'env LC_ALL=C.UTF-8 LANG=C.UTF-8 do-release-upgrade -f DistUpgradeViewText; printf ...; read -r _'。tmux断开仍继续，无需重发升级。不要起第二个do-release-upgrade。

已经通过初始release notes、第三方源禁用提醒。19:52官方计划：151移除、617新装、1891升级、下载3792MB。已阅读移除清单，主要旧ABI库/t64替换，未移除Docker/SSH，注意libfuse2会移除，升级完成需检查libfuse2t64（AppImage）及原fcitx5/wireshark工具兼容。已按既有用户授权确认y开始下载和升级。不再问同一重启权限。日志/var/log/dist-upgrade/main.log可读。工具write_stdin输出ANSI很大，先在functions JS中strip ANSI并取最后15行再text，不直接JSON长原始输出。PTY接下来的配置文件提示要逐项保留SSH/KAI/Docker设定，不盲目yes。

核心接入代码已提交b2eabb9e（30files）；新Viewer的2项切换测试后来已通过，共相关177+2项单元、25集成、8前端。飞书11.14已追加并回读标题，revision103，标题block doxcnzqJQ7y1tlCCTpR1Iw8FZtg；内容如实标注实际GUI验收仍待做。飞书写入/tmp/kai-remote-feishu-write.json，section XML=/tmp/kai-remote-feishu-section.xml，后续应精确更新11.14而非重复append同标题。

云电脑新版选择器已真实出现；Native在独立验收tab点击“交还助理”后显示已连接/观察中，截图docs/results/2026-11-03-remote-computer/cloud-connected.png已提交。cloud仍active、VM未关机。32.45升级完成再选local，验证实际模型GUI操作/接管和云本地来回切换。自己的/API targets JSON临时tab可复用或关闭，不动用户其他tabs。

Viewer输入代次加固：每个键盘/鼠标/剪贴板输入在发送前读当前desktop epoch，而非只依赖2秒观测缓存，旧人工socket在交还/切换后立即被拒绝。回归180passed /tmp/kai-remote-unit-tests-final.log，相关Pyright0error；Viewer v2已部署，其余api/worker/web仍v1。compose SHA更新94d0cf94975225d0d5b71fca18cf9e90b945365b199174fb2dbe1413605ab9aa，发布日志/data/kai-remote-20261103/release-viewer-v2.log。更改未提交（viewer.py+test），需最后commit。

实际node+viewer均cap_drop ALL，初始反向socket UID link mode600对无cap容器root不可达。已修复为transport目录link:root、2750，sshd Match User kai-remote-link 加 StreamLocalBindMask0117，使新Unix socket继承root group并660，其他用户不可达，不增加容器cap。79sshd -t/reload通过，原config备份sshd_config.before-socket-group。正在重启32的kai-remote-link（sudo PTY74212）以验证新socket权限及Control容器真正SSH identity可达。不要只凭host root测试成功声称node可达。

20:00左右发行版仍在下载最后固件/内核，未安装结束；PTY22585继续监控，不要重复启动。tmux目标kai-ubuntu-2404持久。剩余libfuse2t64、GDM保留X11、Docker恢复、重启、agent-desktop状态与真实GUI/人工接管验收等待完成。

20:10 24.04升级继续安装系统库/桌面包；Thunderbird snap迁移提示已按官方升级确认（必须Return为\r，\n未触发对话）。新root维护PTY=69892，升级主PTY仍22585。root shell可以tmux capture-pane -p -t kai-ubuntu-2404看当前提示。不要第二次启动升级。

发现32重启后172.20/16 Docker桥覆盖79。临时/32通过有线192.168.30.254和无线192.168.33.254均无SSH可达；当前临时路由为wifi /32，未持久修改（nmcli因系统库切换暂不可用，不重复修包）。32没有公司VPN进程，仅Clash。用户确认是虎盾VPN，已提供本次登录凭证（仅聊天，不记录）并正在等待其提供登录地址/客户端名称；需要恢复公司VPN，不以Clash握手成功冒充79可达。79旧ssh.sock目前无人listen，GuestGate实际容器测试ConnectionRefused，尚未完成本地接入。

Viewer v2镜像sha256:012e33ea7b6ef2cfab1ab0f0255dd22a852e9e2e6dc5e60eb01f055a1eb99e86，95层；其余4服务healthy。云电脑真实native CUA重连后已连接/观察中。新增deploy/remote-computer/viewer-fence.Dockerfile保存v2构建配方。临时bootstrap-79.py已修正新transport权限和StreamLocalBindMask，但运行目录权限已先前实改，仍需真重连验证新socket权限。

20:22左右系统仍在官方升级，第一内核580DKMS完成，正编译6.8.0-147。用户问Files打不开：dpkg-query确认nautilus46.4及GTK4已解包但尚未配置，无nautilus进程；等待升级完成后验证，不并行修包或卸载重装。sshd暂inactive（发行版升级socket/systemd切换期间），现有SSH master与root维护PTY仍在，最终重启前务必确认ssh或ssh.socket可用。

用户提供的微信路径只有Mac .pkg（Apple签名/公证有效），不能安装Ubuntu。已在同微信msg/file/2025-03找到Ubuntu_tsinclient_2.5.00826.1_amd64.deb，复制到32:/tmp/kai-tiger-client.deb（未安装，等待dpkg升级锁释放）；依赖GTK3/libnotify/NSS/AT-SPI/DRM，未见要求删除系统核心。postinst配置tsinvc-linux/zra服务，需安装后本次用户授权登录。优先新版2.14但本地精确搜索未找到。Mac虎盾已连，不断开、不改。手册登录入口已读，配置和口令均不写repo。官方客户端帮助URL已404，公网Linux下载搜索暂无可信包；可尝试旧Linux包自带官方更新。

Mac临时bootstrap viewer仅127.0.0.1:15945，经SSH master转发32现有私有RFB socket，用于VPN安装登录。非生产验收通道，结束取消SSH forward；正式本地操作与接管仍必须经79。

20:29用户完成虎盾登录。实测32→79 SSH成功，路由tun1；link恢复，新socket为link:root0660，79control容器内GuestGate identity完整匹配machine-id/computer_id/组件pin（exit0）。用户另确认105.79正向也通，不改成依赖Mac的通道。虎盾已安装2.5.00826.1，两个配套服务active，TSINClient用户进程运行、ldd无缺失；不要重复安装或动其登录。

升级器报告系统升级完成，正在最终重启确认。dpkg --audit空，sshd -t通过，ssh.socket/docker/link/display均active，Files46.4和libgtk4均installed、FUSE2t64installed、NVIDIA580installed。选择N跳过324项可选obsolete清理，保留旧Python/兼容库以免破坏用户开发工具。备份GDM配置后显式WaylandEnable=false以保留本轮RFB所需X11，不打开自动登录。用户早已允许最终重启，将按授权执行，随后需核对GPU/Docker/用户桌面/虎盾恢复。

临时Mac屏幕共享连接失败已取消/退出；15945SSH本地转发已取消。CUA在Chrome当前chrome://downloads被工具明确阻止并结束会话，不能绕过限制；后续真实79GUI验收需在允许页面重新开展，不能把root通道identity当GUI完成。

20:40已完成最终重启后核验：24.04.5、6.8.0-147、RTX3090 driver580.178.04，X11桌面/SSH/Docker/display正常，用户再登录虎盾后连接恢复；Control identity复核通过。35个原运行容器有34个恢复，缺失的是AutoRemove=true的临时kai-assistant-feishu-chat-test-db，Docker已自动销毁，不盲目重建测试库。

用户明确要求可以使用agent-desktop，现已安装官方npm 1.1.0到/opt/kai-agent-desktop（不替换全局Node）。已读内置desktop/platform skill，status accessibility granted/supported_surfaces齐全；Files真实窗口完整snapshot、语义Click打开搜索→复核checked→语义Click关闭搜索→复核unchecked通过。GTK4文本字段无语义输入能力，type明确POLICY_DENIED/not_delivered，未盲目物理回退。CLI launch nautilus虽打开窗口但launcher报告handoff无法匹配，不将launch返回成功列为证据。CLI尚未接入KAI模型AX工具，平台目前仍RFB动作。

远端viewer SSH首次失败原因：/home/xiaokai是777，OpenSSH StrictModes拒绝公钥；去掉组/其他写权限为755（非递归，不关StrictModes），公钥后已真实拿到RFB banner。随后发现第二处：DesktopSsh/DisplayTransport精简PATH不含Python，ProxyCommand用裸python3导致真实网页IncompleteReadError。已改SshEndpoint使用shlex.join(sys.executable绝对路径)，增加真正Unix socket回环+空PATH回归，181相关测试全通过、Pyright0错误、Ruff通过。

Viewer诊断v3短暂部署后确认上述PATH原因；compose当前694354035c1332b7095d24639467da241f50cc5751cab014ded8ef8aadf78849。准备改api/worker/control到remote-computer-v2，viewer直接基于该API镜像构建v4（避免层累加）；release脚本CAS+无运行请求/无人工接管检查，运行日志/data/kai-remote-20261103/release-transport-v2.log。待部署实际完成后继续79真实GUI/模型/人工接管验收，不把裸RFB握手当完成。

20:50左右正式79验收：本地桌面网页连接成功；人工控制时KAI computer_key被明确拒绝，用户交还后相同Escape执行成功。随后DeepSeek Flash通过24次computer_*工具调用完成合成页AGENT-READY输入点击，实际页面确认；直接noVNC人工接管、逐键输入human、点确认，实际页面确认human，已保存local-human-input.png并交还观察。3个QA Run均succeeded，安全化工具事件qa-tool-events.json（图像正文省略）。

本次有一次模型误投地址到原有Claude Code终端，其后Esc中断并最小化。已按本次URL定位~/.claude/projects审计，仅4项只读Bash（curl本地页面/链接、ss18881、readlink本次server进程/ls验收目录），无写文件/外部提交/新增任务。不能把全过程写成无误通过。headless AX可避免部分坐标焦点风险，但尚未接入KAI模型工具，已在方案/报告/飞书明确边界。agent-desktop另已建立/usr/local/bin入口，普通用户version实测1.1.0。

用户开始在KAI输入新草稿，已停止抢浏览器、不改草稿。最后云/本地往返切换复核暂待用户空闲；已发async问约30秒切换时机（现在可以/稍后），不可将等待当许可，不可在其电脑Run执行中切换。当前本地target，观察状态，无自己的QA运行。

最终79 API/worker/control v2镜像f1ef067666e48e49946ba9d6c9f472ee08d1a199df5d5f37d5a2a8a9aa73b37f；viewer v4 c714f7098c8b0b2096bb2149dca954a8aae334aa22b1412485236bf89604bb28；二者95层。web仍v1 b39ed08ced64413270f40d1f44709a08c95ab8098419eb296a05376df7ed1d7f。composeSHA c3302a05c1d77d807cadf22b88573a91cda83574e95e6ca4fa91088668a77bf4。健康正常，nginx已reload。飞书11.14已更新真实证据、OS最终恢复、agent-desktop边界、误操作影响与未验往返切换，revision107已回读。报告acceptance.md；代码待最终commit。

收尾：6ffe390d已提交，临时合成HTTP服务停止（DOM页面仍可看），正式link/display仍active；/usr/local/bin/agent-desktop入口验证正常。Mac临时control/viewer/tunnel私钥副本与guest.tar、手册配置纯文本已清理，正式79/32凭证保留。用户关于30秒往返切换时机的async问题仍待回复；不要把等待当许可、不要改其新草稿或正在运行的电脑任务。仅这项切换实页验证未完成，AX集成属于后续适配而非已完成能力。

## 23:16 continuation — current uncommitted work, NOT deployed yet

User added: assistant busy sends default steering/multiple bubbles; assistant history paging/switch lag, no loading-history small text; decouple view vs execution; actually integrate agent-desktop; Bot rail plus far right, archive/hidden management in Settings with deletion; native Grok computer dock/style/animations. Do all +79 real QA+Feishu. No new subagents. Main worktree remains untouched.

Active worktree same kai-remote-computer. New uncommitted implementation:
- task-history readerDriven pagination (assistant only), no auto fill/programmatic scroll loading; cache initial reconciliation, dedupe overlapping pages, preserve expanded cursors and reject stale imports on switch; ETag terminal history bypasses projection via If-None-Match BFF. Initial reuse and detail cache15s; no assistant history loading text. Busy assistant queue defaults steer regardless task preference, early messages wait for channel, independent bubbles, serial delivery/idempotent IDs. Tasks/project prefs kept.
- computer viewTarget vs execution target; explicit ?target websocket authorization; atomic binding hash in desktop state and actions, root per-input checks, unselected PC read-only. Dropdown views, More action assigns execution. Connection retry bounded. New bottom dock and animated neutral glyph, screenshot fade. No real OS sleep API; never pretend network failure is sleeping. Looked at real native Grok app com.anysphere.sand through CUA: bottom view/control pill, rounded preview, waking label. Did not read Vault or send Grok messages. Left Grok in its computer details preview after exiting its existing full screen.
- native_desktop.py stdlib-only closed headless CLI protocol (status/list_apps/list_windows/snapshot/click/type/select), no arbitrary argv/shell/headed/clipboard, pinned root binary, drops to desktop UID+NO_NEW_PRIVS; per-execution AGENT_DESKTOP_HOME private RAM namespace. Shared supervisor flock serializes lifecycle and native actions; root execution scope+expiry check and persistent desktop_epoch barrier fence stale input across human takeover/control-node restart. Child pdeathsignal and bounded process-group drain; raw disposition retained, unknown never auto-retry. computer_ui exposed only Remote driver, same leases+permission+node gate, VM unchanged. Refreshed installed CLI builtin desktop/platform/help refs read. Official repo browsed https://github.com/vercel-labs/agent-desktop (current1.1.0 Linux). Native integration not yet real model verified.
- Bot archive/hidden removed from sidebar, plus far right; Settings #bots component added restores paused/unhidden or confirms delete. New owner/CAS/name checked DELETE specialist; archived-only tombstone deletedAt, removes credential_ref grants, frees old slug, hides lookup/list/updates, pauses linked duties in same transaction; history/Vault retained. New delete test passed, primary profile suite still running when checkpoint written. Need final Ruff/type checks, regression tests and UI tests/settings flow.

Tests before latest Bot UI/model addition: frontend70passed (/tmp/kai-chat-pagination-tests.log), backend/native/view/history/DB91passed (/tmp/kai-chat-native-regression.log), changed computer Pyright0 (/tmp/kai-native-pyright.log), TSC0 (/tmp/kai-chat-tsc.log). New delete suite /tmp/kai-bot-delete-tests.log session83699 (one dot so far), latest UI4files30passed /tmp/kai-chat-latest-web.log. Gate test stale fixture corrected run-fence:2 and isolated engine URL, not weakened production checks. General agui/routes.py has preexisting type errors; changed computer files clean.

Deployment STILL old compose SHA c3302a05..., api worker/control remote-computer-v2, viewer-v4, web-v1. New staging /data/kai-chat-20261103/source. Built image web:20261103-chat-v1 (old prefinalweb source), web:20261103-chat-v2 builds before latest Bot settings/dock; need final rebuild. API/viewer kai/superagent-api:20261103-native-desktop-v1, kai/computer-viewer:20261103-native-desktop-v1 built from pre-Bot-delete code; need final rebuild/tag v2. Do not deploy stale build. Source tar /tmp/kai-chat-release-source.tar; use COPYFILE_DISABLE=1 tar --no-xattrs --disable-copyfile to avoid Apple metadata spam. Staging tar source all package but excludes node_modules/.next/cache. API clean overlay default base6b9 keeps95layers, viewer FROMnewAPI no new layer, web cleanNode9. Native files root pin must be final format bytes.

SSH79 master reconnected /tmp/kai-remote-release.sock root. 32 direct LAN now unreachable; use reverse Unix link via79 instead, still no public TCP port. New admin SSH master /tmp/kai-remote-32-via79.sock xiaokai@kai-remote-32-45, ProxyCommand ssh -S79 root@79 python3 /data/kai-remote-20261103/source/src/harness/computer/socket_proxy.py /data/.../transport/ssh.sock, strict HostKeyAlias192.168.32.45 pinned /tmp/kai-remote-computer-32-45.known_hosts. Root interactive PTY49568 currently sudo -s root@xiaokai (user already authorized; credentials only chat, don't echo). 79 PTY58459 root. Dead direct32 attempt62272 may still pending, don't use.

/tmp/kai-native-stage contains new native_desktop.py, remote_gate.py, activate.py; copied to32 /tmp/{native_desktop,remote_gate,activate}.py. activate.py NOT run yet. Must recopypin files after any edits! Script CAS old image dac63..., verifies no live root lease, root-only backup /var/backups/kai-native-20261103, installs2rootfiles, config native_desktop=true, pinned CLI path /opt/kai-agent-desktop/node_modules/agent-desktop/bin/agent-desktop-linux-x64 andsha, display:0 XAUTHORITY original, newimage hash FOUR originals plus native_desktop.py. Cannot run while worker active. Need guarded deploy: verify0active/nohuman, stop79worker briefly, activate32 viaPTY, capture newimage, update79 compose api/worker remote image+allowed local target image, control remote spec digest+native_desktop=true; CAS compose backup. API/control/viewer/web first, update canonical registry via ComputerRegistry.switch_provider (public lifecycle method, no raw SQL writes) to same remote volume/newimage when idle, then startnewworker. Must rollback32pin/config andcompose on failure. Do NOT break user VM or174. Current remote image olddac stilllive untilactivation; existing services untouched. Last DB read23ish no activeRuns/human (must recheck).

Own synthetic native GTK3 fixture running on32 user service kai-native-acceptance, /tmp/fixture.py. Desktop title KAI 原生控件验收, app is python3.12 (NOT custom prgname). Headless real snapshot via CLI succeeded: only textfield named验收文字 andbutton确认验收, <1KB. No input/click yet (save for model). Viewappprivateexisting apps untouched. CUA native Chrome owngroup Remote Computer验收 mainassistant; remoteChrome binding exists, maininput empty beforeopeningGrok. Must freshAX beforeUIactions. No extension browser attach (earlier left pauseddebugger); no chrome://downloads. CUA initialize docs oncompaction rewriteDocumentation.

Remaining: finishlatesttests, add meaningful Settings restore/delete tests (own synthetic only), code/release plan/report+commits; final stage/build; guarded physical pin+79 deploy; actual model computer_ui synthetic fill/click/readback and human takeover/staleinput validation, zero screenshots count evidence; quick multi-message steering QA, real history switching/paging/nohint, realviewcloud/local while runfixed; Bot settings and bottomdock realpage QA; stoponlyownsyntheticservice afterQA; Feishu11.14 targeted updates actualevidence. Native Grok current screenshotseenbottomdock; sleep notforced. Feishu doc XV4..., heading11.14doxcnzqJQ7y1tlCCTpR1Iw8FZtg, revision107lastknown; mustfetchfresh. Skills lark-doc/shared andrefs readpreviouscontext; lark-docSKILLreadagain23:07, beforeeditingverifyrefs ifneeded. No user-secrets inrepo/docs/output.


## Final release and user handoff (supersedes 23:16 in-progress checkpoint)

User requested direct deployment and manual QA. Commits c073a44e / 7a874ad5 / 3add8d9a / d632a31b built from final source. prepare verified idle and stopped worker; 32.45 activation backed config and updated component pin; finish CAS replaced compose, started healthy API/control/viewer/web, updated same remote registry via public lifecycle method, then started healthy worker and reloaded nginx. No rollback needed. compose SHA7931f25921c4ddb775de4385fe60565aebdc2fa7d3840f87163f177236619b33. Actual image IDs native-release.json, /assistant HTTP200. No user work interrupted; cloud disk/config/chat retained.

Feishu11.14 targeted5paragraph replacements, fresh fetch before each; final readback revision112 verified computer_ui/chat-v3/manual QA. Prior RFB incident and OS evidence retained. Latest automatic tests91 backend/native/view/history/DB and70frontend passed, targeted17passed+oldfixture corrected case1passed, TSC/Pyright computer/Ruff passed. Native model/paging/steering/view-target/settings actual UI QA deferred to user explicitly. Synthetic GTK3 acceptance app remains for user. Do not stop it before user test. No actual OS suspend or token savings measured.

User then asked to investigate missing HTML in Feishu. Read-only DB: run_eaa0c63a66bb4426b14188b991037dad published READY HTML artifact_1132586d20e044e2bd261f4563f05a90, 37659bytes; no assistant_channel_files entry. Original request was Web assistant, no Feishu Incoming linked. Later Feishu turns have sent text receipts but produce no new artifact, so enqueue(current_run_id) sees no file. Model falsely claimed no Feishu attachment integration; actual native upload/file-message transport exists and historical deliveries succeeded. Later GUI Feishu attempt also failed and was unrelated to native transport. No file resent or broad auto-forward policy enabled. Root issue is historical artifact reference/delivery across channels, not file lost or Feishu upload rejection. Current investigation report separate.
