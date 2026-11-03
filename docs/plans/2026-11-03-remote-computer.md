# Remote Computer：云电脑与用户电脑切换

用户授权将192.168.32.45接为本人的Remote Computer，复用现有架构，保留云虚机，部署79并验证真实操作和人工接管。只操作79与该本地电脑，不覆盖共享工作区、不操作174。

工作树：feat/remote-computer-20261103，基线b6368be6。平台继续只管生命周期、权限与消息；此功能不增加业务流程提示，不改变任务/项目的执行环境。

## 实现结构

ComputerRegistry仍是当前绑定的唯一事实来源；运营配置声明两个可选目标，公开API只暴露opaque target ID和名称。切换沿用同一事务/行锁，拒绝活跃执行或人工控制，递增桌面epoch。云VM磁盘和本机系统/用户文件各自保留，不搬移HOME、不关闭机器。

remote-computer执行复用原supervisor的UID28000、PID/mount namespace和执行租约。独立SSH控制密钥只能进入固定root gate；桌面密钥只能转发本人当前:0桌面的私有Unix RFB socket。SSH传输与VM共享实现；桌面队列、接管取消和drain确认沿用原协调器。

网络实测79不能主动连192.168.32.45，采用本机发起到79的反向SSH Unix socket。专用link账号只允许远程Unix转发，禁止TCP转发/TTY/普通命令。控制节点和viewer挂载同一私有socket并核对SSH host key。凭证不写聊天/仓库/模型上下文。

前端沿用noVNC与接管/交还控件，工具栏增加电脑选择。Viewer从同一账号API读取当前目标，旧连接在目标改变时关闭。目标不接受客户端host/path/密钥参数。

## 验证进展

机器/网络检查、私有反向通道和Unix桌面传输已完成。已通过180相关单元、25数据库集成、8相关前端测试、TypeScript、Pyright相关文件0错误。最终发布与真实GUI操作/接管、云本地来回切换、飞书同步继续验收，以最终报告为准。

agent-desktop的辅助功能定位作为后续适配器：接入本地电脑本身复用现有RFB协议；用户随后单独授权将32.45升级到24.04，以满足当前官方Linux支持要求。CLI安装、可访问性探测与模型工具接入分别验收。

## 用户追加：将32.45升级至24.04 LTS

用户已确认保存工作，允许升级期间中断服务和重启32.45。前置22.04更新、配置/软件/服务备份及一次重启已完成；官方24.04.5 LTS升级器的签名已校验。计划下载3792MB，151旧包移除、617新装、1891升级，已按授权开始。发行版升级在root tmux会话kai-ubuntu-2404内持久运行。

升级后检查NVIDIA RTX3090、SSH、Docker及原运行容器、X11桌面、反向连接和KAI观测/操作。注意FUSE兼容库（AppImage）、中文输入法和原第三方源。随后核实agent-desktop在本机24.04的实际支持能力，保留当前RFB观测/输入作为通用适配和回退。不得把安装CLI等同已具备模型端辅助功能树操作。

## 运维边界与当前进度

79的control与viewer保留cap_drop ALL。反向Unix socket目录使用专用link用户、root组、2750；sshd该用户StreamLocalBindMask=0117，socket为link:root、0660。容器通过组访问，不新增DAC能力，不开放TCP桌面端口。Viewer v2每次键盘、指针或剪贴板输入都重新核验当前人工控制epoch，拒绝交还后旧连接继续输入。

32.45重启后Docker的172.20/16网段覆盖79目标路由，且公司VPN尚未恢复，当前不能将远端连接标为通过。必须在真实VPN恢复后核对通道、实际桌面与人工接管；临时路由探测不是交付证据。用户已明确虎盾VPN并授权本次登录，凭证不进入仓库或文档。

## 24.04与辅助功能树的实际边界

32.45已完成24.04.5升级并重启进入6.8.0-147，RTX3090/580.178.04、SSH、X11和Docker验证正常。Files46.4真实窗口可读，agent-desktop 1.1.0由官方npm包安装到独立/opt/kai-agent-desktop。status报告AT-SPI权限与窗口等surface可用，完整Files树及搜索框的语义展开/收起已复核。GTK4搜索框的无焦点type被明确拒绝，不把拒绝误报输入成功；CLI launch对D-Bus转交的返回值也不能代替实际窗口检查。

这证明本机可运行原生agent-desktop，不代表KAI已新增AX模型工具。后续AX适配应放在既有DesktopService协调器内，与RFB输入共享租约/epoch、取消和人工接管屏障；不得让模型通过普通命令直接连接用户session bus，不得另起一套生命周期或按业务写死流程。

真实部署发现两项环境问题并修复：远端HOME原为777，OpenSSH StrictModes拒绝其viewer密钥，只移除组/其他用户的写权限为755，不递归变更文件、不关闭StrictModes；本地SSH代理改用shlex.join组织的绝对sys.executable，在桌面通道的精简PATH下同样可执行。新增Unix socket真正收发、空PATH回归，相关181项单元测试通过。
