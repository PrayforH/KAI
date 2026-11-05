# VM 快速可见操作

目标：Bot 的后台 shell 与可见桌面在同一 VM；复杂网页及桌面应用优先读取结构化可访问性状态，减少截图与视觉推理。默认 VM，本地电脑必须由用户明确选择。沿用同一屏幕、profile、Control/Viewer 和现有接管/交还，不另开隐藏浏览器或新电脑。

参考：

- https://github.com/browser-use/jev-ultrafast ，检查点8251e272398011cdef648965204d1c20cd838333，MIT。借鉴原子状态读取、动态操作/目标集合、节点身份和遮挡校验、按结果等待，默认不把截图送进每轮决策。它的TypeSafe操作/目标模型及可选文字模型有外部服务依赖；项目宣称的7.073秒是特定Google Flights任务证据，不能当作KAI速度承诺。当前不引入新收费模型，也未复制其站点示例。
- https://github.com/vercel-labs/agent-desktop ，Apache-2.0。已有本地电脑固定入口使用同一原生CLI；VM扩展复用该入口的命令allowlist、完整snapshot ref、派发与不确定结果语义。官方Linux条件含图形会话与AT-SPI，当前Debian12必须实际验证，不由二进制存在推断全功能可用。
- https://github.com/vercel-labs/agent-browser 。浏览器专用候选可通过CDP连接现有Chrome；它的默认独立headless启动不能直接用于本次共享桌面方案。

实现切片：

1. 已完成共享VM路由与79模型/UI联合验收，见../results/2026-11-05-bot-shared-vm-desktop.md。
2. VM原生能力显式开启：Worker assistant_computer_vm_native_desktop、Control每台VmComputerSpec.native_desktop；缺省关闭，没有VM镜像不可启用。VM固定SSH gate新增native_desktop/native_barrier分支，调用现有适配器而不进入任意root程序。配置只允许enabled及固定二进制摘要，UID、路径、显示、会话bus均由代码固定；用户请求不能指定它们。标准工作盘、execution lease、桌面epoch、交接drain仍在。
3. 既有VM通过只读维护ISO更新少量root所属组件，保留根盘、工作盘、账号、host keys与浏览器profile。已有组件逐文件备份，失败回退；只安装发行版本身的at-spi2-core，不换glibc或引入其他发行版仓库。私有固定session bus让桌面服务账户的AT-SPI与KAI适配器连接同一图形会话；Chromium显式启用可访问性树。CLI使用已登记电脑相同已校验SHA256二进制。
4. 验收要分别记录纯传输/工具时间和含模型的完成时间；在同一可见页面验证list_windows/snapshot/refs/click/type，记录截图次数。测试接管、迟到动作、失效ref、不确定写入不得自动重放。保留纯视觉/无可访问性控件的截图能力，不把截图压缩当作结构化路线。
5. 后续浏览器专用层：根据实测决定agent-browser私有CDP连接或Jev的固定DOM读取适配。只能连接当前VM的可见浏览器和既有profile；不得公开CDP端口、接受模型任意JS/选择器、另建headless环境或绕开控制权。
6. 后续持续连接/FFI：只有先测出每次CLI/SSH启动是瓶颈，才引入每VM驻留适配器或CLI提供的C接口，继续按每个请求校验租约/epoch并在交还前drain；不能只为benchmark跳过这些检查。

新增首个桌面动作优化：ComputerSandboxProvider 首次 GUI 使用仅获取电脑执行租约，不先调用文件 manifest/stage。首次 shell/文件/ACP 才在每租约独立锁下初始化工作区一次；仅桌面运行不收集旧文件。GUI 后并发文件工具、输入只上传一次、既有文件不误发布、结束释放租约由真实 PostgreSQL 集成测试覆盖。

现有 VM 维护要求：以无人执行、无人持有桌面控制权为前提，停止 Worker/Control/Viewer，正常关机后替换只读 NoCloud 维护 seed，再启动同一 QEMU 与原根盘。ISO 使用 Rock Ridge（保留大小写和精确文件名），附带固定 allowlist 的 SHA256 manifest；不新增登录账号或 SSH key。enable-native-desktop.py 仅更新原生适配器与桌面启动文件，自动逐文件备份，失败回退；二进制版本及摘要由运维预先核对。安装成功后显式开启 Worker 和该 VM 的 native_desktop 两侧能力。裸 Xvfb 必须同时有固定 session bus、ACCESSIBILITY_ENABLED=1 与 Chromium 的 --force-renderer-accessibility，不能仅凭 status 成功判定可用。

检查进展：69项原生/入口单测、21项真实 PostgreSQL GUI/VM/桌面交接/工作区集成测试通过；Ruff及diff检查通过。首轮79已安装CLI1.1.0、启用原生 gate，但真实页面返回空窗口；已定位无完整桌面设置服务时的 Chromium 注册条件，修正启动配置并待二次实页验收。原运行 run_d305fa49bd2b44f691889c35321b4715 的空窗口明确不计成功，尚不宣称速度目标达成。
