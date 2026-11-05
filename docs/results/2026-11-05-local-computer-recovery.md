# 79 本地电脑画面连接恢复

2026-11-05。用户反馈ZCode更新后本地电脑无法连接。维护入口恢复后，确认32.45的kai-remote-link、kai-remote-display都active，79反向Unix socket存在；控制组件、机内声明与实际7文件摘要一致，均为sha256:72b329d672a3ed9aa8d590f667ff63a72c537dc3fb40372f7fca310c11157f50。未因历史文档所述的5文件算法而重新设置摘要。

实际故障是/etc/kai-computer-remote.json为root:root 0640。remote_display_gate以机主桌面用户运行，无法读取声明；普通桌面身份现场读取得到PermissionError。Viewer日志为IncompleteReadError，WebSocket /computer/desktop?target=local-32-45返回403。root执行的控制identity和x11vnc服务本身正常。

仅恢复声明为root:桌面用户主组（现场GID1000）0640。先在/var/backups/kai-computer-recovery-20261105保存root私有备份，逐字节断言声明内容不变；不更改公钥、授权、组件摘要或软件，不停止或重启电脑、隧道、VNC、API、Worker、Control、Viewer及IM，不清理用户文件/浏览器/聊天，不修改Registry或租约数据库。

验证：

- 桌面普通用户可读声明，直接连接私有VNC socket返回RFB 003.008。
- 79真实Computer Control内使用现有私钥/反向通道完成固定identity校验及完整RFB握手，分辨率4096×2304；没有请求截图或发送键盘/鼠标输入。
- 真实Chrome打开79主会话的电脑面板，目标为本地电脑，最终显示已连接；Viewer相同target的WebSocket accepted / connection open。未发送聊天业务消息，未接管或操作远端桌面。

防复发：参考安装脚本从声明desktop_uid解析桌面用户主组，使用install -o root -g该组 -m640安装共享声明；bash -n及git diff --check通过，没有重跑整套安装脚本。演示运维文档已纠正ACP两个文件也在当前摘要内的说明，并强调原子替换后需核对共享声明属组。修复不回退ZCode已完成的ACP演示功能，也未部署上次仍等待空闲的Bot目录/头像新版。

## 同日复发：SSH辅助子进程耗尽容器进程额度

用户再次报告断开后，现场确认共享声明仍为root:xiaokai 0640，32.45隧道/VNC active；79 Control真实身份校验和完整RFB握手也通过。此次不是共享声明再次被覆盖，而是独立的进程回收问题。

从宿主/proc与pids控制器交叉检查：Viewer的Python PID1下积累57个python3僵尸子进程，pids.current=62、pids.max=64；Control下有126个python僵尸子进程，pids.current=131/256。存活线程只有各5个，docker top只看到主进程，不能据此否认累积。Viewer新SSH握手得到IncompleteReadError，并且docker exec也因无法创建进程报OCI init-p connection reset。SSH ProxyCommand的孤儿子进程需要PID1回收，uvloop应用进程没有承担这一职责。

修复：给79私有Compose中的computer-control、computer-viewer启用init:true，保持镜像、声明摘要及64/256进程上限不变；保存0600备份后只重建这两个容器，并重载Nginx解析。事前active Run=0、lease=0、电脑idle；暂停原Worker并复查，原API/Web/IM容器及Worker容器ID保留。恢复Worker时Compose start连带启动了migrate，后者退出255；随即直接启动原Worker并确认healthy，schema复核仍0065，未修改迁移文件或业务数据。源代码新增compose.process-reaping.yaml固化两项设置。

修复后实际PID1为docker-init。70次真实Viewer SSH/RFB banner连接、断开全部通过，超过原64额度；结束后Viewer回到6个任务、0僵尸。20次Control固定identity调用全部通过，Control同样0僵尸。实际Chrome编码协同页点击重新连接后，本地电脑显示已连接，Viewer WebSocket accepted / connection open；未取得控制权、未发送远端输入。页面打开时Viewer任务数为9，Control为22，均0僵尸，未通过提高上限掩盖泄漏。

节点回执：/data/kai-computer-reaper-20261105/release.json、cycles.log、control-cycles.log、process-after.json。Compose SHA256=7c67385130023bb101fd5d765dde96849b62eb1f8446e139ae93f83ad7491092。Control镜像sha256:295560b73d2bdaa4db091a7ca8cb09e8164fe5d6eca5015ca541fbdcb060d2db，Viewer镜像sha256:fedc42e9ddd8d92be9668b0c129adba6a74794a08761a66704844f607e084bd6，均未更新镜像。Compose合并配置校验与git diff --check通过。
