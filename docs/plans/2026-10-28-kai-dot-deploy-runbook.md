# 79 部署 Runbook：同一台电脑 + 桌面控制权（2026-10-28）

对应分支 `auto/kai-dot-20261028`（基线 `6403fff8`，提交 ≥ `d99502e1`）。执行前提：79 SSH 通道恢复（本机 `/tmp/kai79-desktop-style-20261027.sock` 或受权私有配置）。**严禁把任何密钥写进本文档或命令输出。**

## 0. 部署前核对（连接恢复后立即做）

```sh
ssh <79> 'cat /data/kai-79-20261013/compose.private.json' > /tmp/79-compose-current.json
# 备份
ssh <79> 'cp -a /data/kai-79-20261013 /data/kai-assistant-backup-20261028-pre-dot && ls -la /data/kai-assistant-backup-20261028-pre-dot | head'
# 数据库当前版本与备份（隔离库先演练）
ssh <79> 'docker exec <pg容器> psql -U <user> -d <db> -c "select version_num from alembic_version;"'
```

预期：`alembic_version = 0051`。0052/0053 均为新增列/表，向下兼容；回滚 = `alembic downgrade 0051`（0053 的 downgrade 只删新表，无数据丢失面；0052 的 downgrade 在有 retired_images 时会拒绝）。

## 1. 构建（本机，不 push 公网）

```sh
cd <worktree>/agent studio
# API/Worker（含 control_server 新端点；两镜像同源）
docker build -f deploy/assistant-79/api.Dockerfile -t kai/assistant-api:20261028-dot1 --build-arg ... .
docker build -f deploy/assistant-79/web.Dockerfile -t kai/assistant-web:20261028-dot1 .
docker save kai/assistant-api:20261028-dot1 kai/assistant-web:20261028-dot1 | gzip > /tmp/dot-images.tar.gz
scp /tmp/dot-images.tar.gz <79>:/data/kai-assistant-20261028/
ssh <79> 'docker load -i /data/kai-assistant-20261028/dot-images.tar.gz'
```

镜像 ID 与源 commit 记录进验收报告。

## 2. 数据库迁移（先隔离库演练，再正式）

```sh
ssh <79> 'docker exec <pg> pg_dump -U <user> <db> > /data/kai-assistant-20261028/db-pre-0053.sql'
# 正式库升级到 0053（在 api 容器内执行 alembic upgrade head 前先确认 head==0053）
ssh <79> 'docker run --rm -v /data/...:/src kai/assistant-api:20261028-dot1 sh -c "cd /src && ls migrations/versions | tail -3"'
```

## 3. canonical adopt（一次性）

信息来源（部署时现场读取，不写死在文档）：
- computer_id + incarnation：identities 目录名 `/data/kai-computer-identities-20261027/computer_*-*`
- guest base digest：`sha256sum /data/kai-computer-guestbase-20261027/base/*.qcow2`（或既有 config 里引用的 digest）
- 工作卷名：`kai-computer-guestbase-a-20261027`

```sh
ssh <79> 'docker exec <api容器> python /app/scripts/adopt_owner_computer.py \
  --database-url "$HARNESS_DATABASE_URL" --tenant-id <tenant> --user-id <owner> \
  --provider vm-computer --image sha256:<digest> \
  --computer-id computer_<32hex> --volume-ref kai-computer-guestbase-a-20261027'
```

同时**停止旧 Docker Computer 的写入权**：把 `HARNESS_ASSISTANT_COMPUTER_DEFAULT_PROVIDER` 切到 `vm-computer`（见 §4）。旧的 docker-computer 行保留作历史，不删除；新的 Run 按 registry 行路由到 VM。

## 4. 服务配置（compose.private.json 局部叠加，不整栈重启）

API/Worker 两个服务：
```json
"HARNESS_ASSISTANT_COMPUTER_VM_IMAGE": "sha256:<guest base digest>",
"HARNESS_ASSISTANT_COMPUTER_DEFAULT_PROVIDER": "vm-computer",
"HARNESS_ASSISTANT_COMPUTER_IMAGE": "sha256:<保留原值，供回滚>"
```

Computer control 服务（新参数，重启该容器即可）：
```json
"HARNESS_COMPUTER_CONTROL_VM_COMPUTERS": [{
  "volume_ref": "kai-computer-guestbase-a-20261027",
  "container_name": "<79 上现有 VM runner 容器名>",
  "ssh_host": "<该容器在 kai-computer 网络里的 DNS 名>",
  "ssh_port": 2222,
  "runner_image": "<现有 VM runner 镜像引用，含 digest>",
  "guest_base_digest": "sha256:<同上>",
  "root_disk_source": "/data/kai-computer-guestbase-20261027/clones/a/root.qcow2",
  "root_disk_target": "/vm/root.qcow2",
  "work_target": "<现有容器里工作卷挂载点，如 /data/kai-home>",
  "identity_dir": "<控制容器内 identities 路径>/computer_<32hex>-<32hex>",
  "kvm_device": "<现有容器 KVM 设备的宿主 inode 路径>",
  "network": "<VM 所在 docker 网络名>",
  "adopt": true
}]
```

控制服务容器需追加挂载：identities 目录（**只读**，与 viewer 相同的宿主目录）。Web/BFF 无新配置（catch-all 已覆盖）。

切换顺序：先升 control 服务 → API/Worker → Web。逐个 `docker compose up -d --no-deps <service>`，禁止全栈重启。

## 5. 部署后验收（真实产品路径）

1. `GET /v1/studio/assistant/computer/desktop`（带本人登录态）→ `observe`。
2. 网页电脑面板：观察态可见画面；点「接管」→ 2s 内变「由你控制」，键盘可用；「交还助理」→ 回观察态。
3. 微信发“在电脑上创建文件 dot-check-20261028.txt 写入随机代号”→ 真实 Run 在 VM 内执行（`/home/kai/workspace` 出现该文件）；网页电脑打开终端验证文件可见。
4. 人工在网页电脑修改该文件 → 飞书要求读取实际内容 → 回复含修改后的代号。
5. 旧 epoch 拒绝：接管瞬间 Agent 若在输入，工具返回占用提示；跨用户/未登录访问 desktop 端点 → 403/404。
6. 重启 Worker → 刷新网页 → 同一文件仍在（VM 未动）。
7. Worker 重启后 Run 正常完成；VM 重启（`docker restart <runner>`）后电脑面板重连。

## 6. 回滚

```sh
# 服务回滚：换回原镜像 tag（r21），控制服务去掉 VM_COMPUTERS 环境变量重启
# 数据库回滚：alembic downgrade 0051（0053 仅新表）；0052 在无 retired_images 数据时可退
# registry 回滚：UPDATE assistant_computers SET provider='docker-computer' WHERE ... （保留行）
# 绝对禁止：直接重启旧 Docker 实例而不先 fence 新 VM 写入者
```

## 7. 当前阻塞（2026-10-28 03:15 记录）

- 本机无 `/tmp/kai79-desktop-style-20261027.sock`，直连密钥（id_ed25519_173/174、sapling_v20_79_vm_20261025）均被 79 拒绝（Permission denied (publickey)）。
- 已完成：代码、迁移、adopt 脚本、runbook；阻塞在通道恢复。恢复后按 §0→§6 顺序执行，预计 60–90 分钟内完成部署与验收。
