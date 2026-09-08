# Mecha · 品牌与命名

> 2026-08 定稿。输入：飞书《AXIS 开源生态对标与后续发展建议（2026-08）》（定位叙事见六・四）、README 产品定位、两轮评审反馈。
> 总览页：[`preview.html`](./preview.html)（本地 `python3 -m http.server` 打开即可）。

## 定位句（品牌叙事的锚）

「企业可治理的多运行时 AgentOps 控制面 —— 构建、运行、评测、晋级、审计一条链。」

不做清单：不做另一个 Coze/Dify；不做第四家编排框架；不让自然语言直接修改生产版本。

## 名称：Mecha · 智能体机甲

演进路径：`AXIS（弃用：听不出品类，且与 Axis Communications 同名）` → `Agentline（过渡：线的意象不足）` → `Mecha`。

- **命名铁律**（评审沉淀）：名字要自己说出「给谁用、管什么」；要体现组装模式。
- **机甲叙事**：模块（prompt / skills / tools / subagents / MCP）组装成机体（= Manifest 确定性 Bundle）；驾驶舱里始终坐着人（= 审批/暂停/恢复）；试车过门禁才能出厂晋级（= Eval/质量门禁）；黑匣子全程留档（= Trace/审计）。
- **对外写法**：全称 `Mecha · 智能体机甲`；企业语境一律带描述符 `AGENTOPS CONTROL PLANE` 压稳语气；口头叫「机甲」。
- **风险**：软件治理类无巨头同名，注册前需做第 9/42 类商标检索。

### 子系统命名（部件化，随模块发布逐步启用）

| 名称 | 中文 | 对应平面 |
| --- | --- | --- |
| Mecha Frame | 骨架 | 定义面：Draft / 能力目录 / Bundle 编译 |
| Mecha Trial | 试车台 | 质量面：Preflight / Eval / 质量门禁 |
| Mecha Cockpit | 驾驶舱 | 运行面：耐久 Run / 审批恢复 / Sandbox |
| Mecha Blackbox | 黑匣子 | 运营面：晋级证据 / Trace / 审计账本 |

## 标志：机甲头 · 护目轴

主推概念 A，备选 B（轴上合体）、C（合体缝）见 `concepts/`。

- **机体**：横向圆角方 30×24/64，描边 4.4，圆头端点，与控制台线性图标同语言；不画五官，机体感靠比例。
- **护目轴 = 产线**：品牌绿 #25C878 横线从左入、贯穿机体（护目镜/驾驶舱视线）、右出，两端不顶格；是全标唯一彩色。
- **脱线节点 = 放行**：线末端半笔画间隙后脱离一枚节点，即过测晋级的产物离线下线。
- **净空**：四周留 1/8 图高；≤16px 使用简化变体（加粗笔画、省略节点）。
- **禁则**：不拉伸变形；不动绿轴色；不加投影描边。

## 色板

| Token | Hex | 用途 |
| --- | --- | --- |
| MECHA INK-0 | `#202823` | 主底/深色面 |
| MECHA PAPER | `#F5FAF7` | 笔画/深底文字 |
| MECHA AXIS | `#25C878` | 贯穿绿轴（唯一彩色，浅色面用 DEEP 替代） |
| MECHA DEEP | `#15945A` | 浅色面上的轴/强调 |
| MECHA MIST | `#E7F1EA` | 浅色底 |

## 文件清单

```
docs/brand/
├── preview.html                    # 品牌总览页（提案评审用）
├── README.md                       # 本文件
├── mark/
│   ├── mecha-mark-primary.svg       # 主标 · 深色 tile
│   ├── mecha-mark-primary-light.svg # 主标 · 浅色 tile
│   ├── mecha-mark-mono.svg          # 单色 glyph（透明底）
│   ├── mecha-icon-64.svg            # App 图标 / favicon（64/32/24）
│   └── mecha-icon-16.svg            # 16px 简化变体
├── lockup/
│   ├── mecha-lockup-dark.svg        # 横排组合 · 深色
│   └── mecha-lockup-light.svg       # 横排组合 · 浅色
└── concepts/                        # 落选概念留档
    ├── concept-b-modules.svg
    └── concept-c-seam.svg
```

Lockup 中的字标暂以系统 UI 字体呈现，正式交付前需将 `MECHA` 转曲（outline）。

## 落地路径（待执行，本轮只做设计）

1. `web/harness-console/src/components/product-brand.tsx`：`PRODUCT_NAME = "Mecha"`，描述符 `AGENTOPS CONTROL PLANE`，按本主标重画 `ProductBrandMark` 笔画。
2. `web/harness-console/src/app/icon.svg`：替换为 `mecha-icon-64.svg`。
3. `README.md` 标题改为 `Mecha · 可治理的多运行时 AgentOps 控制面`。
4. 代码层 `harness-*` 模块名/包名本轮不动，随发版逐步对齐。

## 落选候选（留档）

| 候选 | 一眼读到什么 | 落选原因 |
| --- | --- | --- |
| AXIS | 抽象中轴 | 听不出品类；Axis Communications 同名 |
| Agentline | 产线 | "线"意象不足，评审未采纳 |
| Governor（御） | 控制论调速器 + 治理者 | 语义准但仍需解释，输给机甲的零解释度 |
| Greenlight（绿灯） | 审批放行 | 只有动作没有平台；同名金融卡跨类风险 |
| Combiner（合体） | 组合器 | 机甲的温和替补 |
| 榫卯 Sunmao | 确定性组装 | 海外拗口；已有同名开源框架 |
