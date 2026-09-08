# 2026-08-09 容器供应链安全验证

## 结论

在 Colima 上构建的最终 `linux/amd64` API、Web、Sandbox 镜像，经 Trivy 0.69.2 的
HIGH/CRITICAL、`--ignore-unfixed` 扫描均为 **0 HIGH / 0 CRITICAL**：

| 组件 | 本地验证镜像 ID | 报告 |
| --- | --- | --- |
| API | `sha256:12afd902850c46f831f9d258c748bb7095118eea562a6acceec8d4f5b53cb09f` | `api-security2.json` |
| Web | `sha256:088f940be311c500e4d587bb76f9368bc87b9abf3e5a358b96aefab3f39fe869` | `web-security3.json` |
| Sandbox | `sha256:ffa13852c119cf9923d4134acd8b7783fdf3bcf428733db6c385c58b38234932` | `sandbox-security2.json` |

174 最终 gray2 实际部署的 Harbor 镜像又按 manifest digest 拉回 Colima，并对精确镜像 ID
复扫；结果仍为 **0 HIGH / 0 CRITICAL**：

| 组件 | Harbor manifest / 本地精确镜像 ID | 报告 |
| --- | --- | --- |
| API | `sha256:ad592bb682ceaa7f4969c0e14ab7dc8d2594c6e32c12618ec2a2295d4a5333e0` | `api-gray2-harbor.json` |
| Web | `sha256:4c783b126d40ee97719036f6b08446f3a917905c848976c5bab4bc35a8a66688` | `web-gray2-harbor.json` |

精确复扫使用 Trivy 0.69.2、OpenVEX、`--ignore-unfixed` 和当日缓存漏洞库；漏洞库
`UpdatedAt=2026-08-09T07:04:26Z`、`DownloadedAt=2026-08-09T12:55:37Z`。扫描时 174
仍运行相同 immutable tag `p1-security-20260809-797d733-gray2`。

这些是 dirty 工作区的本地/灰度证据，不是正式 Release 签名或生产批准。正式发布必须从干净
提交运行 GitHub Release workflow，并经过 test → canary → production 的不可变 digest 晋级。

## 处置记录

- Python 基础镜像扫描为 0/0。
- Node Alpine/Bookworm 基础镜像各为 7 HIGH / 1 CRITICAL，均来自构建镜像自带的 npm
  工具链；Web/Sandbox 最终运行镜像删除 npm 后为 0/0。
- Kubernetes 官方 kubectl 1.35.7 与 1.36.3 基础镜像各为 5 HIGH / 0 CRITICAL；官方
  1.35.7 二进制的 govulncheck 还定位到 10 个可达符号漏洞。替换为签名的 Chainguard
  kubectl 1.36.3 后，镜像 Trivy 为 0/0、govulncheck 为 0 个可达漏洞。
- Chainguard kubectl digest 为
  `sha256:1e1aa9dedf0d9008e5a3710b23f2072bc2ab83117146d503c689b5d2592add3d`；
  Cosign 验证的 workflow identity 是
  `https://github.com/chainguard-images/images/.github/workflows/release.yaml@refs/heads/main`，
  issuer 为 `https://token.actions.githubusercontent.com`。
- `aiohttp` 从 3.14.1 升级到 3.14.3。`cryptography` 保持当时 PyPI 最新的 49.0.0；
  `CVE-2026-69247` 仅影响仓库未使用的 PKCS7 decrypt 路径，使用
  `security/vex/cryptography-49.0.0.openvex.json` 精确标记，并由源码导入回归测试防止边界漂移。
- Web 将 Next.js 升级到 16.2.11，并用 lockfile override 固定 Sharp 0.35.0；镜像构建阶段
  固定 npm 11.6.2，保证 `npm ci` 与 lockfile 生成器一致。

## 运行时核验

- API：Python 3.12.13，非 root UID 10001。
- Web：Node 22.23.2，非 root UID 10001，运行层无 npm。
- Sandbox：Node 22.23.2、Claude Code 2.1.206，UID 1000，运行层无 npm。
- kubectl：在签名来源二进制上完成 govulncheck；Colima arm64 上通过 QEMU 运行 amd64
  kubectl 曾出现 Go runtime 段错误。真实 x86_64 的 174 API 容器已完成运行 smoke：kubectl
  v1.36.3、Go 1.26.5、`linux/amd64`，二进制 SHA-256 为
  `be120a946253634fdee1b6ae85b41c5563e53a3499e1a1bfe98524a2ca591e09`。因此该异常已确认
  属于 Colima/QEMU 仿真路径，不是部署二进制缺陷。

## 证据索引

- `*-base.json`：变更前/候选基础镜像扫描。
- `api-security2.json`、`web-security3.json`、`sandbox-security2.json`：最终镜像扫描。
- `api-gray2-harbor.json`、`web-gray2-harbor.json`：174 实际部署 digest 的精确复扫。
- `api-security2-vex-audit.json`：API VEX 审计输入输出。
- `govulncheck-kubectl-v1.35.7.txt`：官方二进制可达漏洞。
- `govulncheck-kubectl-chainguard-v1.36.3.txt`：替换后二进制无可达漏洞。

所有 JSON 都保留扫描器目标、精确镜像 ID 与具体包版本；漏洞库更新时间按上述 metadata
单独记录。正式 Release 必须重新在线更新漏洞库后扫描，不能只复用本摘要或缓存结果。
