# 79 助理回复中断：目录兼容性回归修复

2026-11-04，北京时间。编码协同两次同内容请求在启动模型前失败：run_b6a99bf640fd47319b28dd83199dfcfc、run_a530a2617fc245c4b754da17b6059ddf，错误为 CapabilityCatalogRecord 校验失败，invalid Agent model bindings: lead-agent。

前次 ACP 输出投影小切片部署把功能分支的整个 catalog_service.py 复制到了线上，覆盖生产已有的兼容实现。分支版本把仍在用的 deepseek-v4-flash 等配置路由列入自动删除，未保留 lead-agent 的绑定，同时丢失旧分组路由的凭据迁移。服务/healthz仍正常，真正发起Run时目录投影失败。这是发布回归，与用户提示、Claude登录、本地电脑网络无关；此前的健康检查与ACP单次冒烟不能覆盖后来加入的目录文件。

先在无活跃Run/电脑执行租约时将API/Worker恢复到前一ACP镜像，目录与Bot入口返回200；保留Web、Control、Viewer和IM、电脑绑定和schema0065。之后恢复生产兼容目录逻辑：只处理既有的已替代旧入口，不自动删其他配置模型；对被Agent绑定的入口保留路由和凭据，须先显式重绑再退役，不静默替换模型；旧DeepSeek分组拆分仍继承原凭据引用。

新增回归覆盖system和管理员编辑目录、现用DeepSeek旧ID、已绑定退役入口、凭据和Agent选择保留、重复get幂等、旧分组凭据迁移。目录/模型配置76项通过，受影响源码及新回归Pyright零错误，Ruff和diff check通过。旧测试中依赖默认旧模型名称的fixture已改为当前默认预设，删除/禁用已发布路由仍验证会被拒绝。

发布候选镜像在与API相同网络和环境下，只读取得现场Catalog，再在独立内存仓库上执行升级：生产9条路由→候选9条路由、所有Agent绑定及所绑定路由内容保留，重复get相同；无生产写入。与仅检查健康不同，此预检执行了实际持久配置的校验和升级路径。

最终API/Worker为kai/superagent-api:20261104-catalog-92ee117c，sha256:a47533bba4639e5f52f322361eaf53b9c2d26eeb99ddd65a573491d04fcff83d。发布目录/data/kai-catalog-92ee117c，preflight.json、release-public.json；compose SHA256 4ab973c732b349c2f32d8f3adb04a1918abdbe5549bd1c181817545e907b6ae3。活跃Run/租约归零并停Worker复查后切换，健康、nginx校验和reload通过。Viewer、飞书、微信容器ID未变，未迁移数据库、未改机主账号/文件/浏览器，不操作174。

真实Chrome进入编码协同，原请求保留，点击一次“重试回复”。新Run run_8927f93ef1984b07a2dbdc40b2ce0b4c 已实际进入remote-computer和模型/工具执行，越过原先的目录校验失败点；最终执行及交付情况续记下文，不以running当作任务全部完成。

最终原请求已成功：Run run_8927f93ef1984b07a2dbdc40b2ce0b4c为succeeded；真实computer_acp(agent=claude)返回协议比较初稿，Bot生成outputs/acp-protocol-comparison.html并发布artifact_4668002e66de413b8e531a0d041e8f99。飞书文件投递工具最终回执status=sent、errorCode=null、messageId存在；不是仅凭Bot自述。Chrome实页已看到完成回复和“ACP-协议对比说明.html”的预览/下载附件入口。飞书11.21修复说明revision128已读回核对。

附件展示同步收敛（01f1ef28）：对照本地Grok Bot现有报告，保留内容预览及附件入口，去除KAI额外生成的“文件/成果发布/飞书投递”大汇总卡。仅对存在附件的主/子助理summary展示生效，补充/确认/授权表单、HTML预览保留；普通任务和项目任务继续原分支。相关前端61项通过，TypeScript与生产构建通过。发布前将两份完整UI文件与线上构建源逐一比较，确认仅含目标改动。

79仅更新Web为kai/superagent-web:20261104-bot-file-card-01f1ef28，sha256:978787f3ff4d844cb6a5d930633d74b25b1d9b6422e5e477843dd02e198b225b。发布脚本和回执在/data/kai-catalog-92ee117c/release-bot-file-card.py及release-bot-file-card.json；最终compose SHA256 b25df4eff68cd90ceea1d1632a2e04d531ad5a79420648bcc6ef4ce33c2f9d50。API、Worker、Control、Viewer、飞书、微信容器ID均断言未变，后端仍为catalog-92ee117c；Web健康和nginx校验通过。

真实Chrome打开原编码协同会话，AX与截图均确认大汇总卡已消失，同一ACP-协议对比说明.html预览/下载附件保留。飞书方案追加原请求成功交付与附件展示验收，revision129已按关键词读回核对。代码提交在PR80，目标develop，仍不声称已合入或全仓门禁通过。
