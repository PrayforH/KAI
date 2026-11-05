# 79 本地电脑画面连接恢复

2026-11-05。用户反馈ZCode更新后本地电脑无法连接。维护入口恢复后，确认32.45的kai-remote-link、kai-remote-display都active，79反向Unix socket存在；控制组件、机内声明与实际7文件摘要一致，均为sha256:72b329d672a3ed9aa8d590f667ff63a72c537dc3fb40372f7fca310c11157f50。未因历史文档所述的5文件算法而重新设置摘要。

实际故障是/etc/kai-computer-remote.json为root:root 0640。remote_display_gate以机主桌面用户运行，无法读取声明；普通桌面身份现场读取得到PermissionError。Viewer日志为IncompleteReadError，WebSocket /computer/desktop?target=local-32-45返回403。root执行的控制identity和x11vnc服务本身正常。

仅恢复声明为root:桌面用户主组（现场GID1000）0640。先在/var/backups/kai-computer-recovery-20261105保存root私有备份，逐字节断言声明内容不变；不更改公钥、授权、组件摘要或软件，不停止或重启电脑、隧道、VNC、API、Worker、Control、Viewer及IM，不清理用户文件/浏览器/聊天，不修改Registry或租约数据库。

验证：

- 桌面普通用户可读声明，直接连接私有VNC socket返回RFB 003.008。
- 79真实Computer Control内使用现有私钥/反向通道完成固定identity校验及完整RFB握手，分辨率4096×2304；没有请求截图或发送键盘/鼠标输入。
- 真实Chrome打开79主会话的电脑面板，目标为本地电脑，最终显示已连接；Viewer相同target的WebSocket accepted / connection open。未发送聊天业务消息，未接管或操作远端桌面。

防复发：参考安装脚本从声明desktop_uid解析桌面用户主组，使用install -o root -g该组 -m640安装共享声明；bash -n及git diff --check通过，没有重跑整套安装脚本。演示运维文档已纠正ACP两个文件也在当前摘要内的说明，并强调原子替换后需核对共享声明属组。修复不回退ZCode已完成的ACP演示功能，也未部署上次仍等待空闲的Bot目录/头像新版。
