# Vibe Radar Phase 2 提案：一键试玩运行时（Try Runtime）

| 项目 | 内容 |
|---|---|
| 文档性质 | 已采纳（2026-10-13）：作为独立 Phase 2 立项；修订清单已并入 PRD 3.2/4.5/2.2/M7 与 USER_STORIES US-09、附录 E34–E38 |
| 日期 | 2026-10-13 |
| 背景 | 用户提出"终端式"方向：读者在日报中看到产品即可一键安装到工作区并试玩 |
| 与现有文档的冲突 | 3.2 已将"自动安装试玩包"明确后置；4.5 规定"MVP 不自动执行第三方仓库安装脚本" |
| 提案判断 | 方向成立且与 3.3 的验证目标同源，但应以独立 Phase 2 立项、本地优先，不并入当前日报 MVP |

## 1. 动机：把"从发现到体验的时间"压到近零

现状链路：看到卡片 → 判断门槛 → 打开仓库 → clone → 装依赖 → 申请并配置 API key → 跑起来 → 触达核心体验。后四步是主要流失点（待试读数据验证，当前为假设）。

可复用的既有资产（见 README「LLM 接入与配置窗口」）：

- LLM 档案与 Gateway（`data/llm.json`、`llm/gateway.py`、`POST /api/llm/chat`）——用户只需配置一次模型；
- 成本账本与预算上限（`ops/cost.py`）——可按试用会话复用；
- 核查与验证体系（`contracts/enrichment.py` 的 `verification_status`）——天然适用于"配方是否实测过"；
- 反馈事件体系（`feedback/events.py`）——可为"玩过了"提供真实证据。

差异化假设（待验证）：把用户已配置的 LLM 通过本地代理注入试用环境，是"一键体验"链条里最难被复制的一环。

## 2. 产品设计

### 2.1 可试性分级（try_tier）

不让"能不能装"成为二元例外，而是与 pricing/login 并列的一等字段；未知不承诺。

| tier | 条件 | 读者侧承诺 | 实现 |
|---|---|---|---|
| `instant` | 在线入口/纯静态/单文件 | 打开即玩（沿用现有 30 秒口径） | 复用现有 `direct` |
| `container` | 标准 Node/Python 等，配方可声明 | "一键启动"；预计时间仅在配方已有实测中位数时显示，否则"准备时间未验证" | Docker 容器 |
| `compose` | 需数据库/多服务，但可声明 | "一键启动（较慢）" | docker compose |
| `guided` | 环境复杂/长编译/GPU | 不承诺一键；给分步本地指引（不执行） | 仅文档 |
| `blocked` | SaaS/硬件/候补/授权不明 | 看演示 | 同现有 `demo` |

规则：tier 由配方核验决定，不由 LLM 现场判断；显示必须早于跳转（延续 4.4）；时间承诺只引用实测记录（延续 `time_promise` 校验逻辑）。

### 2.2 配方（TryRecipe）：一次核验，长期复用

反对"LLM 现场推导安装步骤"——慢、脆、不可审计。采用配方制：

- LLM 起草（从 README/仓库提取），人工核验一次后冻结并版本化（复用 `config_versions` 模式）；
- 用户侧一键 = 拉镜像/构建 + 起容器 + 挂载工作区 + 注入代理地址；
- 腐烂对策：记录 `verified_at` 与最近成功时间；连续失败自动降级为 `guided` 并进入复核队列。

示例（`config/recipes/<slug>.yaml`）：

```yaml
slug: context-garden
tier: container
repo: https://github.com/owner/repo
image: node:20
install: [npm ci]
run: [npm run dev]
port: 3000
healthcheck: http://127.0.0.1:3000
llm_env:                      # 工具需要模型时注入，见 2.3
  OPENAI_BASE_URL: "{{proxy_base}}"
  OPENAI_API_KEY: "{{virtual_key}}"
timeout_minutes: 8
license_checked: MIT
verified: {by: curator, at: 2026-10-13, device: "win11+docker27"}
```

### 2.3 LLM 注入：代理模式，沙箱永不接触真 key

- 沙箱只拿到 `virtual_key` 与本地代理地址；真实 key 由 Gateway 在服务端注入；
- 每次会话一个可吊销虚拟 key，绑定会话预算（复用成本账本），超预算即拒绝；
- 代理兼容 openai 与 anthropic 两种协议（`llm/client.py` 已支持）；
- 会话内模型用量记入现有账本，同时作为"配方实际可用"的证据之一。

### 2.4 隔离与工作区

- 每个会话独立根：`data/trials/<session_id>/`（`VIBERADAR_DATA_DIR` 之下），**绝不在仓库工作树内安装**；
- 容器约束：非 root、只读基础镜像 + 仅工作区可写、CPU/内存/PID 限额、除会话目录外无宿主挂载、无 docker socket；
- 网络：安装期允许声明过的包源；运行期是否默认受限见 §7 开放问题；
- 生命周期：ready 后 TTL（初值 2 小时）自动回收；`viberadar try clean` 显式清理；会话目录不进备份；
- 红线：真实 LLM key、宿主个人凭据与浏览器会话一律不进沙箱。

### 2.5 终端式体验

- CLI：`viberadar try <slug>` 实时输出安装日志；`viberadar try status|stop`；
- Web：`/try/{slug}` 用终端视图流式显示日志；状态机 `preparing → installing → ready → failed|timeout|stopped`；失败给出可复制日志；
- 原则：慢不可怕，黑盒等待才可怕——始终显示当前阶段、已用时，预计时间仅在可信时显示。

## 3. 与现有实现的衔接（改动点清单）

| 层 | 新增/修改 | 说明 |
|---|---|---|
| contracts | `TryTier`、`TryRecipe`、`TrialSession` | `enrichment.py` 增加 `try_tier` 可选字段，不替换 `TryMode` |
| store | `try_recipes`、`trial_sessions`、`trial_events` 表 | 配方版本化；会话绑定 product/edition_item；事件关联反馈 actor |
| `trial/`（新模块） | 运行时检测、配方执行器、会话管理、清理 | 与 `pipeline/` 隔离，不进入日报主流程 |
| llm | 会话代理端点（虚拟 key 校验、预算、吊销） | 复用 `llm/gateway.py` 预算与账本 |
| feedback | `tried` 事件增加可选 `trial_session_id` 证据字段 | 自报与会话证据分开存储，统计口径公开说明 |
| ops/cli | `try / recipe list|verify / trial status|stop|clean` | 破坏性操作 dry-run 惯例不变 |
| web | `/try/{slug}`、卡片 `try_tier` 标签 | 未核验配方不显示"一键" |

## 4. 落地阶段

### P2A 本地验证（零运维，先证明有人用）

- 范围：Windows + Docker Desktop；仅 `container` 级；人工维护 5–10 个高频精选配方；CLI 优先。
- 完成标准（可核对，不虚构）：
  1. 干净机器上 5 个配方端到端跑通，会话可回收、宿主无污染；
  2. 红队清单通过：配方无法读取真 key；尝试外传环境变量的测试配方被网络策略或代理设计阻断，逐项记录；
  3. 记录每个配方的 time-to-ready，形成第一批实测中位数。

### P2B 产品化接入

- 日报卡片显示 `try_tier` 与实测时间承诺；`/try` 页面进入静态站；
- 指标入 `status`：配方成功率、time-to-ready、失败阶段分布、试用会话数/期号条目；
- "玩过了"增加可选会话证据；`try_tier` 是否作为排序信号待评测后再议（避免"好装≠好产品"偏差）。

### P2C 托管沙箱（需求证明后再立项）

- 触发条件：P2B 指标显示持续使用；否则不做；
- 该阶段是运维产品（隔离、滥用、配额、计费、合规），需单独提案；对匿名用户是否开放属于开放问题。

## 5. 风险与对策

| 风险 | 级别 | 对策 |
|---|---|---|
| 任意代码执行（供应链、挖矿、勒索） | 高 | 容器隔离 + 资源限额 + 最小挂载 + 安装后断网；不接受"逐行审计代码" |
| LLM key 外泄 | 高 | 代理注入虚拟 key；会话预算；可吊销；红队清单 |
| 配方腐烂 | 中 | `verified_at` + 连续失败降级 `guided` + 复核队列 |
| 一人团队运维过载 | 高 | 先本地、后托管；托管有明确触发条件 |
| 许可与法律 | 中 | 配方记录 license；不公开发布再分发镜像 |
| 排序偏差（好装优先） | 低 | `try_tier` 只展示、不默认加分，评测后再定 |

## 6. 非目标

- 不做"任意仓库即装即跑"（只执行已核验配方）；
- 不做 GPU、长时间编译与 Windows 原生复杂环境（`guided` 兜底）；
- 本提案不改日报主流程、不加账号体系；
- 不把 LLM 草拟的配方未经人工核验直接上架。

## 7. 开放问题（待产品负责人决定）

1. 宿主范围：仅 Docker Desktop？是否要求 WSL2？是否提供远程 Linux 主机模式？
2. 运行期网络：默认断网会破坏部分工具；按配方声明域名白名单的粒度与维护成本是否可接受？
3. 匿名用户是否可使用托管试用？若可，滥用模型与配额策略是什么？
4. "玩过了"证据口径：启动成功 ≠ 试玩；是否采用"手动确认 + 会话存续"的组合，并写入 PRD M7？
5. 是否把 `try_tier` 纳入排序信号？若纳入，需先建评测集。
6. 配方核验节奏：每期新增上限多少条，由谁负责？

## 8. 对现有文档的修订清单（已执行，2026-10-13）

| 文档 | 修订 |
|---|---|
| PRD 3.2 | "自动安装试玩包"从后置项改为本提案定义的 Phase 2 范围，注明安全边界 |
| PRD 4.5 | 增加 `try_tier` 显示规则；"不自动执行第三方仓库安装脚本"限定为"未经核验配方" |
| PRD 2.2 / M7 | "玩过了"增加可选会话证据口径 |
| USER_STORIES | 新增 US-09：一键试玩（含失败、超时与清理场景） |
| 附录 E | 新增验收编号（建议 E34–E38）：配方执行、隔离、超时降级、代理注入、清理 |


## 2026-10-14 工程落地记录

P2A/P2B 工程链路已加入仓库，含 CLI、Web、配方版本化、隔离约束、预算代理、日志、
TTL 和会话确认。实际容器与五个精选配方验收因本机缺少 Docker Desktop 尚未完成。
五个候选保持 guided，不宣称已核验或实测时间。
见 [实现记录](../implementation/FEED_AND_TRIALS.md)；P2C 不在本轮实现范围内。
