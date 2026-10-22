# 79 助理增量发布

API/Worker 从已验证的 174 API r54 镜像构建，复用其锁定依赖和执行内核；替换工作树中完整 harness 源码及迁移。Web 在 amd64 79 的 Node 22 构建，运行镜像复用已验证的 Web r60。构建前必须核验基线镜像 ID（见基线验收报告）、Node 架构，并排除 node_modules/.next/.venv 等本地产物。macOS 打包必须设置 `COPYFILE_DISABLE=1`，使用 `tar --no-xattrs --exclude='._*'`，防止 AppleDouble 文件被 Alembic 当作 Python 迁移读取。基线 API 镜像有固定入口；独立迁移命令必须显式使用 `--entrypoint alembic`，Compose migrate 则使用 `entrypoint: []`。

发布顺序：备份当前私有 Compose 和实际业务库 → 克隆库迁移 0044 → 隔离预览和认证 API 检查 → 无活跃运行时短暂停应用 → 正式库升级 → 切换三个应用镜像 → 健康、认证、数据计数及真实事项执行验收。数据服务不变。失败恢复前一 Compose，保留增量表和新数据供检查；禁止自动恢复整库覆盖用户数据。

飞书尚未配置应用，本发布只显示待配置和接入指引，不启动真实消息收发。
