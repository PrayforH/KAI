# 79 共享VM原生控件操作

用户希望助理与电脑预览共享同一可见桌面，并借鉴Jev或Vercel工具减少逐步截图。此次复用已接入本地电脑的Vercel agent-desktop适配，将原生观察和语义动作接到现有持久VM，不另起headless浏览器。实现e0e9078e、f2d711a1、7014428a、0ff3b6c1；共享电脑路由见2026-11-05-bot-shared-vm-desktop.md。

## 实现与边界

VM能力在Worker与Control的目标声明两侧显式启用；固定SSH gate只增加原生协议分支，保留工作盘校验、执行租约、控制代次、人工接管排空与不确定投递不重试。原生CLI使用经摘要校验的1.1.0二进制，以桌面用户运行；无模型指定的PID、系统路径、root命令、任意CLI参数、JS或公开CDP。

裸Xvfb的桌面启动增加固定session bus、ACCESSIBILITY_ENABLED=1及Chromium renderer accessibility。仅在VM原生入口进入固定kai-desktop.service的PrivateTmp挂载命名空间，确认服务PID的UID与cgroup，随后降权；不关闭PrivateTmp、Chromium沙箱或窗口身份校验。模型对早期失败的“Chromium嵌入桌面壳／孪生link”解释不是取证结论，实际修复是会话可达性与完整身份观察预算。

VM原生snapshot/list_windows及引用动作默认提供8000毫秒上限，提前完成立即返回；允许500–8000毫秒显式调整。入口只在VM增加此字段，旧本地电脑不广告或隐式收到新参数，32.45组件及摘要不变。identity_unproven引用不得用于输入，complete=false不能作为控件不存在的证据。

仅GUI工具不再先扫描／上传工作区；首次shell、文件或ACP调用在独立锁下初始化一次。只有实际初始化工作区才收集变更，不误发布原有文件。VM与本地执行选择、普通任务及项目任务边界保持。

## 真实页面与模型联合验收

在79真实Chrome的独立验收标签，通过工时填写原会话发送公开测试请求，不登录、不读凭据、不执行禅道业务。运行run_f639208143b846f1914bedb7deb74d1a成功，provider为vm-computer，结束lease released。桌面打开现有Chromium及example.com后，仅用computer_ui完成完整快照、按新引用点击Learn more及目标页再观察；右侧实际显示https://www.iana.org/help/example-domains与Example Domains。目标页快照complete=true，173个引用；没有以截图或坐标代替该段原生点击。精简、无图片字节的回执见2026-11-05-vm-native-desktop-evidence.json。

| 本次单次测量 | 工具请求至返回 |
| --- | ---: |
| Example Domain完整快照 | 2849.2 ms |
| 原生引用点击Learn more | 5302.5 ms |
| 点击后窗口列表 | 1170.1 ms |
| IANA目标页完整快照 | 3253.3 ms |

这些是包含租约、数据库、SSH和CLI的工具墙钟时间，不是纯传输耗时或稳定基准。整次冷启动请求从排队到结束102.755秒，包含打开桌面浏览器、模型思考与一次历史上下文整理；不声称达到Jev特定Flights演示的7.073秒。截图只用于启动浏览器阶段，四次；原生导航阶段零截图。先前旧共享桌面首截图13231.8ms，延后工作区后的一次首截图2683.9ms；两次缓存／启动条件不完全相同，不能由此宣称固定提速比例。

最终默认参数暖启动运行run_37b5e67fcece4a62ad7cecad4e6dd15d成功、lease released：4次computer_ui调用，无显式timeout_ms、无截图、无坐标。模型读取Example Domains、按新引用语义点击About，并以完整新快照读回About us；右侧实际显示iana.org/about和About IANA。排队至结束28.738秒，含模型和工具；快照3567.7ms、点击3978.0ms、目标快照3808.7ms。点击本身的投递回执为delivered_unverified/retry:unsafe，成功结论来自后续快照和真实画面，未重复点击。回执见2026-11-05-vm-native-desktop-default-evidence.json，其中requested_timeout_ms为null表示模型未指定，VM适配层注入已验证的默认上限。

## 检查与发布

81项相关单测、24项独立真实PostgreSQL集成测试通过，涵盖原生能力声明、闭合协议、固定命名空间、控制权、租约释放、GUI不扫描盘及首次并发文件输入、VM默认等待与旧本地协议兼容。受影响文件Ruff、bash语法及diff检查通过。未声称全仓类型门禁通过或PR已合入develop。

79最终API/Worker为kai/superagent-api:20261105-vm-native-v6，sha256:39cf9918661682cc9214cc935565d902351bc7b1879c584185ba5a1e8bbce898；Control为kai/computer-control:20261105-vm-native-v5，sha256:7258233eaf67dadb110af5f39e617befd1eebce46691771e2a03649a9c5e6c7d。Compose摘要3acb00aa3c5a1411e4911bcff26b11b339a7d05ddd8b3d94163287b88ef7e27b。API117层，低于本轮120层保护上限；后续镜像应从正式基线整体重建，不能无限叠补丁层。

全部维护均先核对无运行／无执行租约／无人工控制，再正常关闭并启动同一VM；只读NoCloud维护ISO逐文件验证和备份，根盘inode与QEMU容器保留。第2版ISO在文件校验阶段失败，未覆盖guest文件，已恢复服务并改用Rock Ridge重新验证；第3–5版安装成功，不将失败轮次计成功。最终Web、Viewer、IM、数据库等容器ID保持，Control/Viewer保留init回收；未触碰174或用户在32.45上的开发目录、浏览器、登录及凭证。

节点发布与维护回执分别在/data/kai-vm-native-20261105-v5及/data/kai-vm-native-20261105-v6；根盘、浏览器profile和文件保留。凭据解密和执行取用仍暂停。

## 后续切片

以同一可见Chrome为前提，评估agent-browser受控私有连接或Jev原子DOM读写，减少整窗AT-SPI身份普查与每次CLI/SSH开销。必须保留节点身份、遮挡／页面变化校验、执行租约、接管屏障；不接受模型任意JS或公开调试端口。按页面和操作分别采样，报告P50/P95、模型时间、截图次数和实际页面结果；输入、选择、Canvas、iframe及复杂弹窗仍需专项实测，不由本次链接点击推断普适支持。新增VM的官方CLI打包和版本检查应进入镜像流水线，保持双侧显式开关，不能把旧guest都标记为已安装。

飞书方案第11.25节已以用户身份更新并局部读回确认（revision135）；保留11.14历史证据，明确最新默认VM和原生支持边界。
