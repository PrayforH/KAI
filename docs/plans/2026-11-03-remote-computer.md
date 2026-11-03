# Remote Computer：云电脑与用户电脑切换

用户授权将192.168.32.45接为本人的Remote Computer，复用现有架构，保留云虚机，部署79并验证真实操作和人工接管。只操作79与该本地电脑，不覆盖共享工作区、不操作174。

工作树：feat/remote-computer-20261103，基线b6368be6。平台继续只管生命周期、权限与消息；此功能不增加业务流程提示，不改变任务/项目的执行环境。

## 实现结构

ComputerRegistry仍是当前绑定的唯一事实来源；运营配置声明两个可选目标，公开API只暴露opaque target ID和名称。切换沿用同一事务/行锁，拒绝活跃执行或人工控制，递增桌面epoch。云VM磁盘和本机系统/用户文件各自保留，不搬移HOME、不关闭机器。

remote-computer执行复用原supervisor的UID28000、PID/mount namespace和执行租约。独立SSH控制密钥只能进入固定root gate；桌面密钥只能转发本人当前:0桌面的私有Unix RFB socket。SSH传输与VM共享实现；桌面队列、接管取消和drain确认沿用原协调器。

网络实测79不能主动连192.168.32.45，采用本机发起到79的反向SSH Unix socket。专用link账号只允许远程Unix转发，禁止TCP转发/TTY/普通命令。控制节点和viewer挂载同一私有socket并核对SSH host key。凭证不写聊天/仓库/模型上下文。

前端沿用noVNC与接管/交还控件，工具栏增加电脑选择。Viewer从同一账号API读取当前目标，旧连接在目标改变时关闭。目标不接受客户端host/path/密钥参数。

## 验证进展

机器/网络检查、私有反向通道和Unix桌面传输已完成。已通过177相关单元、25数据库集成、8相关前端测试、TypeScript、Pyright相关文件0错误。最终发布与真实GUI操作/接管、云本地来回切换、飞书同步继续验收，以最终报告为准。

agent-desktop的辅助功能定位可作为后续适配器。本机Ubuntu22.04与其当前官方Linux支持要求不匹配，本轮复用现有RFB协议，不为接入强制升级用户OS。
