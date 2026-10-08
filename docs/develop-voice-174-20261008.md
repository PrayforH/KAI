# 最新 develop 与实时语音合并发布到 174

正式入口：https://172.20.109.174/。语音改动已推送 `develop`；部署代码为 `79d2245b214f9e02206ae16a54a5e957f6530ce1`，父提交为最新知识库/紧凑输入栏改动 `d2269396`。使用独立工作区整合，原目录的游离 HEAD 和其他未提交修改保持原样。

## 发布行为

保留 develop 的文档与 Wiki 混合检索、紧凑输入栏与智能体控件。语音麦克风位于模型名右侧、发送按钮左侧；录音控件在原工具栏显示取消、波形与停止。实时识别文字写入原输入框，停止后文本精修替换同一片段，保留前后文且不重复追加。取消恢复本次选区内容；人工修改的语音片段优先于后续自动结果。只有实际长文字触发普通输入框增高，没有额外草稿区、状态提示、错误文案或计时。

流式 ASR 仍使用 229:18013，音频采集、PCM 上传、SSE 与文本精修保持已验证链路；停止后不重新识别音频。新镜像由 174 从整合源码构建，npm 使用国内镜像配置和匹配 lockfile 的公开离线缓存。

## 验证

后端相关 135 项测试通过，全部 src 的 Ruff 检查通过。前端 159 个文件中 1022 项通过、1 项跳过，TypeScript 和 Next.js 生产构建通过。Pyright 在相同虚拟环境下的 develop 基线与候选版本均有 27 项既有诊断，没有新增诊断；不能将其称为完整类型检查通过。

正式 HTTPS 入口使用公开测试音频作为虚拟麦克风，停止前约 3.5 秒出现目标短句（此样本测量值）；实时文字、原前后文和手动补充内容保留、精修同段替换、取消回退、空识别静默、麦克风设备恢复、深浅色/窄屏及正常自动增高通过。没有录制用户真实麦克风。

主 Web、API、3 个 Worker 都健康。API 与每个 Worker 的 341 个源码文件 SHA256 与部署提交一致。切换时活动任务数为 0，数据库仍为 0037，无新迁移。环境变量完整保留，仅更新镜像引用。3301 Web、3401 Codex Web、173、数据库服务和模型渠道未变更。

## 部署与回滚

发布目录 `/data/kai-develop-voice-20261008`。镜像为 `kai/axis-api:develop-voice-20261008`（API + Worker）和 `kai/axis-web:develop-voice-20261008`。现有 `compose.deepagents-174.yaml` 的 API/Worker 镜像及 `compose.voice.yaml` 的 API 镜像已更新，后续沿用原 compose 文件启动不会回到旧版本。

完整环境与旧配置仅存在服务器私有备份内，不在仓库保存凭据。旧 Web `axis-web-voice-20261008-r8` 保留为停止状态；原后端镜像保留。回滚后端：

```bash
cd /data/kai-develop-voice-20261008
docker compose -p agent-studio-174 -f rollback.private.json up -d --no-deps --pull never --force-recreate --scale worker=3 --wait api worker
```

回滚主 Web 时，先停止 `axis-web-develop-voice-20261008` 再启动 `axis-web-voice-20261008-r8`；恢复长期启动配置可使用 backups 中的原 `compose.deepagents.before.private.yaml` 与 `compose.voice.before.yaml`。没有迁移需要降级。

1 个临时测试账号及其默认智能体、版本、刷新令牌已删除，审计日志保留。2 个验收容器及临时 HTTPS 端口已移除。

[机器可读结果](results/develop-voice-174-20261008.json)、[浏览器验收日志](results/develop-voice-174-20261008-browser.log)、[录音截图](results/develop-voice-174-20261008-recording.png)。
