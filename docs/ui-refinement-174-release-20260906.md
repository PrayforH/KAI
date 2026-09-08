# 界面细化发布 · 20260906-144300

正式环境：172.20.109.174:3501。

## 变更

- 品牌标志移至左上角。侧栏收起时显示在顶部左侧，不再放在右上角。
- 标志保持深浅主体，仅使用局部蓝色斜边。新资源 `public/brand/kai-mark-v2.png` 同步网页标签和加载标志，保留旧图。素材及最终提示词见 `docs/brand/kai-accent-refinement-20260906.md`。
- 移除“正在处理”前的三点动画，保留状态、计时和真实过程内容。
- 任务详情、帮助弹层去掉浏览器主机地址。智能体构建助手与配置页清除写死的 174/Worker 环境文案，并去除无法由 UI 保证的“无外网/隔离执行”表述。没有改动实际工具范围或权限。
- 联网核查与②+④方案见 `docs/network-egress-review-20260906.md`；本次不修改网络配置。

## 验证

- 前端 74 个文件、488 项检查通过，Next 生产构建通过；修改文件 diff whitespace 检查通过。
- 隔离浏览器环境验证：左上角品牌、侧栏收起后位置、任务详情无环境地址、构建助手无部署编号。运行中“正在处理”正常显示，DOM 中 `.run-pulse` 数量为 0。
- PNG 为 1254 × 1254 RGBA，透明通道存在。生产容器资源与本地 SHA256 一致：`fa7f7fdc835bf25944b1ef02b54f4cc758247213d3f943aad91149013ff3dcad`。
- 预发布与正式站点：首页、icon.svg、品牌 PNG、auth/config、runtime-config 全部 HTTP 200，API healthz 为 200。
- 正式 Web、API、三个 Worker 均 healthy、restart 0。API 和 Worker 保持上次发布版本，未重启。预发布容器已清理。

## 部署与回滚

- Web 镜像：`kai/axis-web:20260906-144300`。
- 镜像 ID：`sha256:5719a97e05607ec7ba9373382cc30e59596b047437faf9ddbd3bab58a4b82659`。
- 容器：`axis-web-20260906-144300`。
- 构建源码：`/data/kai-release-20260906-144300`。
- 使用既有验证依赖镜像离线构建，不改依赖、数据库、已发布智能体或网络权限。
- 回滚：停止 `axis-web-20260906-144300`，启动保留的 `axis-web-20260906-132229`。原 3301 站点保持原状。

174 只读联网检查结果：三个 Worker 均为 local/remote_cli、allow_unsafe_local_sandbox=true；Docker bridge 非 internal，DOCKER-USER 链直接 RETURN。只能证明当前执行链路未由项目实施按任务的强制代理出口，不能据此推断公司上游网络完全开放。
