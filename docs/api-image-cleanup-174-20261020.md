# 174 API 镜像历史清理

按用户要求清理 174 本机的 API 历史镜像，并解决多次增量构建触达 Docker 层数上限的问题。单纯删除历史镜像不会减少当前镜像的继承层数。

## 当前与回滚

- 当前 API：`kai/projects-api:dictation-20261020-r38`，1 层，`sha256:9e7f1a3ce2024dc9544db96ef3f2b9209e6939c5b37e63fb45617ec56ba28146`。
- 上一功能版本：`kai/projects-api:rollback-helpers-r35-flat`，1 层，`sha256:486fa604d8cd975e2e8babefb4f2c550a74c28ffa73587edec61aa51aee3309a`。
- 压平前的语音 API 为 126 层；首次增量构建达到 128 层，创建容器失败，已恢复原服务后改为单层更新，再完成压平。

压平从未启动的源镜像容器导出文件系统，不从运行中的用户容器制作快照。加载新镜像后逐字段校验原镜像 `Config` 完全一致，包括 User、Env、Cmd、Entrypoint、Healthcheck 等；SDK 仍为 0.2.168。API 启动健康，完整录音接口再次验证通过。切换前确认运行任务和旧实时语音连接均为 0。

当前 API 和长期 Compose 的 API、migrate 镜像均指向新的一层镜像。Worker 保留原 r35，没有改动数据库、对象存储或持久化卷。回滚用私有 Compose 保存于服务器发布目录的 `rollback-runtime.private.yaml`，不入库。

## 清理结果

删除 19 个未被容器引用的旧 `kai/projects-api` 镜像标签：失败及过渡版本 r36/r36a、helpers r31–r35、SDK r27–r30、projects r3/r5/r6/r9/r20/r21/r22 和初始 4466b2a。删除前排除所有仍被运行或停止容器引用的镜像，以及当前、回滚两个保留镜像；未使用强制镜像删除。旧的已退出 migrate 容器一并清理，后续从新 Compose 创建。

已删除压平时的两组临时文件系统/镜像归档，共 6,034,814,976 字节（约 6.0 GB）。这不是把共享镜像的逻辑大小累计成磁盘回收量。详细清理结果保留于服务器 `/data/agent-studio/releases/dictation-20261020-r36/image-cleanup.json`。

网页构建还遇到本机 Colima 磁盘耗尽。清理了该分支 27 个已发布的旧 Web 镜像本地副本、过期无标签镜像及未使用构建缓存，保留 r35 及所有容器引用的镜像，未清理卷。Colima 磁盘由 100% 恢复到约 89%。Harbor 中的已发布镜像没有删除。
