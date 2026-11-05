# Remote Computer 与 Ubuntu 24.04 验收记录

2026-11-04更新：历史文件显式投递、消息滚动区和自动历史加载已部署，HTML的飞书sent回执已确认；最新版本与边界见[本批报告](../2026-11-04-files-and-chat/acceptance.md)。以下保留前一批证据。

此前增量：API／Worker followup-v1、Web followup-v3已发布并健康，/assistant HTTP 200；连续消息、SDK中断调整与具体实时状态见[continuous-followups.md](continuous-followups.md)。原生电脑Control／Viewer继续native-desktop-v2。飞书已单独整理为11.15，回读revision118。

当前版本已部署79。用户最新要求自行手动验收；以下旧版本RFB现场证据保留，不能代替本次原生控件、消息和分页的真实网页验收。

## 最新发布与手动验收交接

实现提交c073a44e、7a874ad5、3add8d9a、d632a31b。API／Worker／Control与Viewer为native-desktop-v2，Web为chat-v3。实际镜像及Compose校验见[native-release.json](native-release.json)。API、Worker、Control、Web健康，Viewer运行，/assistant HTTP 200；32.45固定组件校验同步更新。云虚机、聊天、配置、绑定与文件保留。

自动验证：91项后端／原生控件／电脑查看／历史及隔离数据库回归、70项前端测试通过，相关TypeScript、电脑模块Pyright和改动生产文件Ruff通过。Bot删除／主助理／目标契约相关17项通过，另一个旧测试替身缺少归属字段，修正后该案例复验通过。未声称仓库全量类型检查无错误。

computer_ui已通过平台原有租约、权限和接管屏障连接32.45的agent-desktop，提供有界控件树及语义操作；云电脑维持原路径。远端固定协议、二进制校验、降权、进程清理及持久控制代次约束动作；未知投递不自动重试。不支持的GTK4语义输入返回真实拒绝。本次模型实测和token节省比例尚待验证。

已查看本地Grok Bot原生客户端。电脑控件移至底部，增加预览圆角、连接动画及画面淡入；没有真实OS休眠／唤醒接口，不把网络故障显示为休眠，未让32.45休眠。

交给用户的手动清单：

- 运行中连续补充2—3条，每条独立气泡、直接调整方向；普通任务／项目仍各自独立。
- 快速切助理并向上翻页，确认历史按需加载、位置稳定、不串消息，没有加载历史小字。
- 机器人加号靠右；归档／隐藏只在设置→机器人管理。用临时测试Bot验证恢复、删除。
- 底部电脑控件、预览／全屏、连接动画；执行时仅切换云／本地查看，原执行机不变；空闲时“更多→供助理使用”才变更执行机。
- 32.45合成窗口“KAI 原生控件验收”（应用python3.12）里，输入AX-OK、点击“确认验收”并读回；运行详情核对computer_ui。合成窗口暂留供用户验收。
- 人工接管阻止助理输入，交还后恢复。以上本次真实页面／模型检查均待用户验证。

飞书11.14已同步并回读至revision112，含当前实现、发布版本及待手动验证边界。

## 以下为前一版本现场记录

状态：24.04升级与重启、真实本地桌面、模型GUI输入点击、人工键盘鼠标、人工接管拒绝与交还后恢复已实测。云→本地→云往返切换最后一项待用户当前电脑操作空闲后完成。模型出现一次跨窗口焦点误投，影响已核查并保留为可靠性问题。

## 发布范围

仅79应用与192.168.32.45电脑。未改174，未覆盖共享main工作区。云虚机及原有磁盘保留。实现分支feat/remote-computer-20261103，基线提交b2eabb9e、c48a9610；最终代码提交见Git记录。

79此前API/Worker/Control（remote-computer-v2）：sha256:f1ef067666e48e49946ba9d6c9f472ee08d1a199df5d5f37d5a2a8a9aa73b37f。Web（v1）：sha256:b39ed08ced64413270f40d1f44709a08c95ab8098419eb296a05376df7ed1d7f。Viewer（v4）：sha256:c714f7098c8b0b2096bb2149dca954a8aae334aa22b1412485236bf89604bb28。API、Worker、Web、Control健康。Viewer直接基于最终API构建，二者均95层，Web9层；避免按每次修补累加viewer层。Compose SHA：c3302a05c1d77d807cadf22b88573a91cda83574e95e6ca4fa91088668a77bf4。

## 已有证据

- 181项Computer/remote viewer单元测试、25项隔离数据库集成、8项前端回归通过；相关Pyright、TypeScript通过。
- 真实79页面出现云电脑/本地电脑选择。云电脑实时画面连接、交还观察通过，见cloud-connected.png。
- Viewer输入另增即时epoch检查，交还后的旧人工连接不能依赖缓存继续输入；专用回归覆盖此场景。

以上不能替代本地电脑GUI验收。

## 现场验证

32.45最终启动24.04.5 LTS、6.8.0-147、RTX3090 driver580.178.04。dpkg审计空，Files46.4和GTK4均installed，真实Files窗口可打开，X11/SSH/Docker和虎盾原有客户端保留。35个原运行容器中34个恢复；缺失的是AutoRemove=true的临时kai-assistant-feishu-chat-test-db，原容器已由Docker自动销毁，未盲目重建测试数据库。

真实79网页选本地电脑、连接32.45当前桌面成功。正式验收通过KAI电脑工具和noVNC，未把直接SSH或测试CLI代替产品通道：

- 人工控制时模型尝试computer_key Escape，工具明确返回“用户正在控制电脑；请等待交还后再试”，未送达桌面。
- 用户交还后，同一模型computer_key Escape执行成功。
- DeepSeek Flash通过24次真实电脑工具调用，在浏览器打开loopback合成页面、输入AGENT-READY、点击确认。实际页面显示“已确认：AGENT-READY”；所有Run工具事件见qa-tool-events.json（图像正文已省略）。
- KAI页面接管后，真实指针/逐键输入human并点击确认，实际页面显示“已确认：human”，截图local-human-input.png。随后已交还观察状态。
- 云/本地来回切换最后复核等待用户草稿和正在操作的电脑空闲，不抢输入或改草稿。

### 本次模型误操作与影响核查

模型最初将loopback地址误输入另一Claude Code终端，触发该终端Agent的4项只读Bash调用：读取本地18881页面、枚举该页链接、查看18881监听、读取本次验收server进程工作目录和列出本次验收目录。随后模型按Esc中断并最小化该窗口，回到Chrome完成验收。已按该次URL定位审计记录，未发现文件写入、外部提交或新增后台任务；终端自身不是此次验收创建的服务，不关闭用户原有窗口。不能把这条流程写成无误通过。

这是基于截图与坐标的跨窗口焦点风险。agent-desktop适配应增加窗口/元素身份校验，同时继续受当前DesktopService租约和接管屏障约束，不能靠写死提示词掩盖问题。

## agent-desktop实测边界

官方npm agent-desktop@1.1.0已安装到/opt/kai-agent-desktop，与原Node环境隔离；version返回linux/x86_64，status报告accessibility granted及window/sheet/alert/menu等可用。已读取内置desktop与Linux skill，读取Files完整AT-SPI树，并以语义Click展开搜索→复核checked→关闭搜索→复核unchecked。

GTK4搜索框未暴露语义文本输入，type返回POLICY_DENIED、not_delivered，未将其误报输入成功；nautilus的D-Bus启动转交也使CLI launch返回无法匹配，实际窗口则正常。当前KAI模型工具仍使用原RFB适配器，尚未新增AX模型工具。CLI安装和原生元素操作证明不是端到端AX集成证明。

## 数据与运维

升级前配置/boot/dpkg及服务和Docker清单备份于32.45的/var/backups/kai-ubuntu-2404-20261103，仅root可读。这不是完整根盘镜像。只停止/恢复本次升级必要服务，不重建用户HOME或Docker卷。

反向socket目录为link:root、2750，socket为link:root、0660；sshd专用用户StreamLocalBindMask0117。Control与Viewer仍cap_drop ALL。x11vnc明确禁用IPv4和IPv6 TCP监听，仅本地Unix socket；初配发现IPv6 5900额外监听后已立即停止修正。

首次重启后发现Docker172.20/16覆盖79路由，且公司VPN未连接。用户完成虎盾登录后，路由经tun1恢复，32→79 SSH实测成功；79实际Control容器的远端身份核验exit0，与登记machine-id、computer-id及组件pin完全一致，新Unix socket权限0660。用户另确认79正向可达。最终重启后仍需重新验证登录与通道，不能仅记录服务active。

## 收尾状态

当前选择本地电脑，已交还观察状态；本次3个QA Run均结束，默认DeepSeek Flash未改。合成HTTP服务已停止，页面不含真实账号或业务数据；原浏览器、窗口、虎盾和持久连接保留。Mac临时屏幕共享及15945转发均关闭，本机临时密钥副本和手册配置纯文本已清理；79/32正式私有凭证保留各自受限位置。普通用户可直接运行agent-desktop（/usr/local/bin入口）。

飞书方案11.14已同步并回读至revision107，含实际证据、AX尚未集成的边界、误输入及影响、往返切换待验状态。实现提交6ffe390d，工作树其余变更仅为本报告收尾。用户正在使用KAI编辑新消息，最后往返切换需等待其明确空闲，不打断当前操作。
