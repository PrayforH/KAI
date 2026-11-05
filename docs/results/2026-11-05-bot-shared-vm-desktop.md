# Bot 与用户共享 VM 桌面

问题：除编码协同外，既有 Bot 多数未声明 execution_environment，仍执行于临时沙箱；右侧却显示持久 VM，导致用户与助理看到不同浏览器。截图里的 Grok 回复仅作为交互参考，未当作执行指令。

已实现：已获账号电脑访问的自有 Bot 会话缺省继承账号当前执行电脑；显式 sandbox/未知环境、未授权能力、生命周期异常、其他所有者、普通任务和项目会话保持原边界。电脑默认设置和现场 canonical 目标均为 vm-computer，本地电脑仍须显式选择。没有按 Bot 名称写业务分支或通过解析业务文本选择机器。

VM 内有可见桌面和受执行租约约束的后台 shell，两者属于同一台 VM；桌面操作经 computer_* 与预览共享屏幕，纯 shell/文件操作不会自动弹窗口。桌面账户与任务账户的 HOME/权限分离，不能由“同一台机器”推导桌面文件管理器可直接访问 /home/kai/workspace。模型能力说明明确这项限制；工作区产物已有聊天附件预览/下载链路。GUI 优先复用可见浏览器及其会话；明确要求的后台测试、检索或纯文件处理可无窗口完成，不把它们声称为已打开桌面。

验证：真实独立 PostgreSQL 授权/GUI/目标选择/交接47项，VM路由及桌面租约12项，工具代理/SDK假传输/运行时/原生接口61项，合计120项通过；扩展SDK测试覆盖local/daytona/docker-computer/vm-computer/remote-computer。同步测试替身hooks的既有preface参数以匹配当前协议。Ruff与diff检查通过。定向Pyright使用当前venv后剩sandbox_tools.py既有图像envelope窄化8条诊断，均在本次未改的223–228行；不声称全仓类型检查通过。

79已发布第一版API/Worker sha256:a38f784e2ab7f0b36567d8f81521caf43849c9914fc88b49cdd7c005b900a7a4，Control sha256:1037aacb53327c0e3056cf45bc6bda51d30e0bf6e964f5a036bd4658556359ff；Compose ce4be8bf7a0264316d95cd5efe26fa6999672d724442727cd80a8b44d28524c0。从线上已核对模块摘要的原镜像仅覆盖相关模块；Web/Viewer/IM等容器ID保留，Control/Viewer init:true保留，未动174、32.45及VM根盘、登录配置、聊天历史。发布前后核对空闲、0065和无执行租约，仅停止并恢复自己的Worker。

真实Chrome在独立验收标签发送普通自然语言请求，工时填写运行run_15625fad3de543c9884497416876ffbc成功，sandbox.provisioned与sandbox_leases均为vm-computer，结束lease released。它调用computer_screenshot/click/type/key在VM可见Chromium打开example.com，右侧实际显示Example Domain及kai_shared_desktop=20261105参数；同一Run的Write/Bash生成并读回outputs/shared-desktop-check.txt，聊天出现24字节附件。没有登录、访问凭证或执行禅道业务，原标签用户草稿保留。模型回复中“桌面文件应用可直接看到工作区”未实证，因此追加VM能力说明纠正，不把该句算作验收证据。

上述 VM 权限说明已随 kai/superagent-api:20261105-vm-native 发布到79（sha256:3014cf02ab37f2d5999b5d74034e252006b2517da12f75975cf721d5e36ac3da）；快速操作扩展与后续版本另见 ../plans/2026-11-05-vm-fast-computer-use.md。共享桌面节点证据目录/data/kai-shared-desktop-20261105。
