# Vibe Radar

面向中文 AI builder 的每日新品策展：从 HN / GitHub / 人工注入等发布事件中筛选值得体验的产品，
生成有依据的中文体验指引，通过网页归档与飞书分发，并用匿名反馈改进精选。

本仓库已按 PRD v1.3 落地 MVP 实现（Python 3.11+，单实例 SQLite 起步）。
产品规则、验收场景与口径以 [完整 PRD v1.3](PRD.md) 与 [首发用户故事](docs/product/USER_STORIES.md) 为准。

## 文档入口

| 文档 | 用途 |
|---|---|
| [完整 PRD v1.3](PRD.md) | 需求基线：模块规则、数据契约与 38 个验收场景 |
| [实现说明与验收对照](docs/implementation/IMPLEMENTATION.md) | 模块映射、E01–E33 覆盖状态、DoD 清单与已知边界 |
| [首发用户故事](docs/product/USER_STORIES.md) | 9 个实际场景（含 Phase 2 试玩运行时）、用户价值、验收条件与试读观察方式 |
| [试玩运行时提案（Phase 2）](docs/proposals/TRY_RUNTIME.md) | 一键试玩方向、安全边界与 P2A–P2C 阶段 |
| [评测集说明](evals/README.md) | 评测目录规范、门槛与留出集纪律 |

## 已实现能力（对照 PRD 第 6 章）

| 模块 | 实现 |
|---|---|
| M1 采集 | HN Algolia（Show HN 全量窗口分页）、GitHub 分层 query、人工注入 JSONL、Product Hunt（凭据+条款核查后启用）；按源水位、失败隔离、429/5xx 退避 |
| M2 实体解析 | URL 归一化与跟踪参数清理；GitHub repo 身份与子路径映射；共享平台/同名负例保护；合并记录可撤销、旧 slug 301 |
| M3 分类 | 规则先排明显纯新闻；LLM 批量结构化分类（0–1 置信度、spam 分数）；阈值层级 0.8/0.5 与人工复核（宽进 soft 下仅作质量标记，strict 下拦截） |
| M4 富化 | 公开页面/README 抓取（URL 安全边界）、证据摘录（≤200 字）、门槛字段推断、确定性与模型指南；中英双语卡片与具体用户故事（缺失语言用模板兜底，不混排）；claim 引用完整性程序检查 |
| M5 排序 | 按源中位并列秩分位数、速度、`exp(-h/18)` 新鲜度、五维 rubric、taste_match、spam 惩罚、奖励合计 ≤0.08；确定性公式 |
| M5.5 复核 | CLI 候选列表（≤60，同分类 ≤30）、通过/淘汰（带原因）/编辑/锁定/撤销、时长统计、保存不丢 |
| M6 编制分发 | 冻结快照（短刊/空刊/降级）、受控 `/go/{link_id}` 跳转、飞书 webhook 与发送账本、网页/飞书渠道解耦、unknown 不盲目重发 |
| M7 反馈 | 第一方匿名 ID、事件去重、试玩与评价分离的当前态（改选/撤回）、原因可选、内容报错队列与更正关联、限流 |
| M8 存档 | `/`、`/d/{date}`、`/p/{slug}`、`/saved`、`/feedback/{id}`、`/rss.xml`、sitemap、静态导出；中/英双语切换（`?lang=` + cookie 记忆，任何语言模式不混排）；重点推荐→分组→最近期号三级结构；360px 移动可读、无 JS 可读正文 |
| M9 运维 | CLI（ingest/pipeline/review/freeze/publish/deliver/replay/eval/backup/restore/status/llm…）、调度器（07:30/08:35/09:00/09:05）、互斥锁、成本账本与预算上限、备份与隔离恢复 |

## 架构拓扑

```text
                          ┌──────────────────────────────────┐
                          │ Scheduler（单 worker 进程）        │
                          │ 每 4h 采集 → 07:30 管线 →          │
                          │ 08:35 冻结 → 09:00 发布 → 09:05 重试│
                          └────────────────┬─────────────────┘
                                           ▼
 ┌──────────────────┐      ┌───────────────────────────────────────┐
 │ 采集适配器         │      │            Pipeline Worker             │
 │ HN / GitHub      │─────▶│ normalize → entities → classify        │
 │ 人工注入 / PH*    │      │ → enrich（证据/门槛/指南）→ rank        │
 └──────────────────┘      │        ▲                    │          │
                           │        │   LLM Gateway      ▼          │
                           │        └─ 缓存/预算/多 provider 候选池≤60 │
                           └────────────────┬───────────────────────┘
                                            ▼
                          ┌──────────────────────────────────┐
                          │ SQLite（WAL + BEGIN IMMEDIATE）    │
                          │ 原始条目·产品·事件·证据·期号快照·    │
                          │ 复核版本·发送账本·反馈·成本账本      │
                          └───────┬──────────────┬────────────┘
                                  ▼              ▼
                   ┌──────────────────┐  ┌──────────────────────┐
                   │ CLI 复核（人）     │  │ 冻结快照 → 静态导出      │
                   │ review/freeze    │  │ 首页/日期页/RSS/sitemap │
                   └──────────────────┘  └──────────┬───────────┘
                                                    ▼
                   ┌──────────────────┐  ┌──────────────────────┐
                   │ 发送账本 → 飞书    │  │ Caddy（HTTPS + 静态）   │
                   │ webhook（可重试）  │  │   ↕ FastAPI            │
                   └──────────────────┘  │ 读者页面 / /go 受控跳转  │
                                         │ 事件与反馈 API          │
                                         └──────────┬───────────┘
                                                    ▼
                                         ┌──────────────────────┐
                                         │ 读者浏览器             │
                                         │ 匿名反馈 · 收藏(localStorage)│
                                         └──────────────────────┘
```

- `*` Product Hunt 适配器代码就绪，凭据与条款核查通过后才启用。
- 部署形态：单实例，Web 与 Scheduler 分进程，SQLite 持久卷；Caddy 只公开静态站与读者 API。
- 代码对应：`ingest/`→采集适配器 · `pipeline/+rank/+llm/`→Pipeline Worker · `compose/`→冻结与导出 ·
  `deliver/`→发送账本与飞书 · `web/`→FastAPI/Caddy · `feedback/`→事件与当前态 · `ops/+jobs.py`→CLI 与调度。
- 数据流与故障边界详见 [实现说明与验收对照](docs/implementation/IMPLEMENTATION.md)。

## 快速开始（Windows PowerShell）

```powershell
# 1) 虚拟环境与依赖
py -3.13 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"

# 2) 运行测试（159 项：契约/单元/集成/验收）
.\.venv\Scripts\python.exe -m pytest tests -q

# 3) 人工注入两条候选并跑一条端到端管线（离线 mock LLM）
.\.venv\Scripts\python.exe -m viberadar inject --file examples\manual-sample.jsonl
.\.venv\Scripts\python.exe -m viberadar pipeline --llm none

# 4) 复核、冻结、立即公开（不含飞书外发）
.\.venv\Scripts\python.exe -m viberadar review --list
.\.venv\Scripts\python.exe -m viberadar freeze --date (Get-Date -Format yyyy-MM-dd)
.\.venv\Scripts\python.exe -m viberadar publish --date (Get-Date -Format yyyy-MM-dd) --now --skip-feishu

# 5) 启动站点
.\.venv\Scripts\python.exe -m viberadar serve --port 8000
# 打开 http://127.0.0.1:8000
```

默认 `VIBERADAR_LLM_PROVIDER=auto`：有 `data/llm.json` 档案时用真实模型，否则离线 mock（规则分类 + 确定性模板）。
接入真实模型（OpenAI 兼容或 Anthropic 均可）：

```powershell
# 方式一：CLI 写入 data/llm.json
python -m viberadar llm set --name work --preset deepseek --api-key sk-...
python -m viberadar llm use --name work
python -m viberadar llm test --name work        # 一次最小连通性调用

# 方式二：配置窗口（浏览器）http://127.0.0.1:8000/admin/llm ，直接填 API Key，无需任何令牌/环境变量

# 或旧式环境变量直连 DeepSeek
$env:VIBERADAR_LLM_PROVIDER="deepseek"; $env:DEEPSEEK_API_KEY="..."
```

生产建议同时设置 `VIBERADAR_STRICT_LLM=1`（无模型维度不得自动刊登）。

## LLM 接入与配置窗口

同一份 LLM 能力服务三类用途：本工具管线（分类、兴趣评估、用户故事/指南、期号开场白）、
其他工具（Python 库或 HTTP 端点）、以及用户自配模型（配置窗口）。

- 配置文件：`data/llm.json`（随 `data/` 一并 gitignore；密钥只落本地文件，不进数据库与日志）。
- 档案（profile）：`protocol` 支持 `openai`（OpenAI 兼容，含 DeepSeek/Kimi/GLM/Ollama）与 `anthropic`
  （Claude Messages）；每档配 `model_fast` / `model_strong` 与可选价格（美元/百万 token）。
- 阶段映射：`classify/interest` 默认 fast，`enrich/compose` 默认 strong；可在窗口或
  `viberadar llm stage --stage enrich --tier strong` 调整。
- 复用方式：`from viberadar.llm import LLMClient` 直接调用；其他语言/工具走
  `POST /api/llm/chat`（本机直连免令牌；远程调用需 `VIBERADAR_LLM_API_TOKEN`，共享缓存、预算与成本账本）。
- 预算与成本：延续日/月硬上限；档案 `price` 缺省时可回退 `config/pricing.yaml`，都未定价则成本记 0
  并在状态中标注，不阻塞调用。
- 安全：设置页 `/admin/llm` 与 `/api/admin/*` 默认开放（本地工具），公网部署由 Caddy 统一 404
  `/admin/*`、`/api/admin/*`；`/api/llm/*` 本机直连免令牌、远程需令牌。

```powershell
python -m viberadar llm list                 # 档案、激活项与阶段映射
python -m viberadar llm presets              # 内置预设（deepseek/openai/anthropic/moonshot/zhipu/ollama）
python -m viberadar llm set --name kimi --preset moonshot --api-key sk-...
python -m viberadar llm test --name kimi     # 连通性测试
curl -X POST http://127.0.0.1:8000/api/llm/chat `
  -H "Content-Type: application/json" `
  -d '{"system":"你是助手","user":"用一个词回答：连通正常吗？","json_mode":false}'
```

## 前端验收（可复跑）

```powershell
python tools/seed_acceptance.py   # 可选：昨天常规刊 6 条 + 前天空刊
python tools/qa_frontend.py       # 44 项断言（含中/英切换与用户故事）+ 桌面/360px 截图（logs/qa/）
```

服务已在运行即可执行；覆盖首页分级、短刊/空刊、产品页、反馈与收藏交互、404、移动端可用性。
详细清单见 [实现说明与验收对照](docs/implementation/IMPLEMENTATION.md)。

## 每日运行

```powershell
# 调度器（单 worker；APScheduler，Asia/Shanghai）
python -m viberadar scheduler

# 或由任务计划/容器按分钟调用一次到期任务
python -m viberadar scheduler --once

# 状态总览（源水位、阶段、发送账本、成本）
python -m viberadar status --date 2026-10-12
```

调度时间线（PRD 5.1）：每 4 小时增量采集 → 07:30 截点冻结并跑管线 →
08:35 冻结编辑版本（应用人工修订层）→ 09:00 网页原子公开 + 静态导出 + 飞书投递 →
09:05 失败告警与有界重试（unknown 只核对不盲发）。

## 运维命令要点

```powershell
python -m viberadar replay --run-id <run_id>            # 默认 dry-run，沿用原窗口
python -m viberadar deliver --date 2026-10-12 --channel feishu
python -m viberadar retry-deliveries                    # 仅重试 retryable_failed
python -m viberadar correct --date 2026-10-12 --item-id <id> --fields '{\"access_text\":\"收费\"}' --reason "读者报告费用错误"
python -m viberadar issues                              # 报错队列（当日复核）
python -m viberadar merge --from <slug> --into <slug> --reason "同产品证据"
python -m viberadar backup                              # 含 manifest 哈希；保留 7 日 + 4 周
python -m viberadar restore --backup-dir <dir> --staging <dir>
python -m viberadar eval --dataset-version v1           # 缺少样本时拒绝生成虚假指标
python -m viberadar llm set|use|stage|test|list          # LLM 档案与阶段映射（详见上文）
```

所有命令输出运行目标、输入窗口与是否外发；破坏性操作默认 dry-run 或需要显式确认。

## 目录结构

```text
vibe-radar/
├── PRD.md / README.md / docs/        # 需求与故事基线
├── config/                            # sources/taxonomy/rubric/pipeline/pricing/prompts（冻结 hash）
├── src/viberadar/
│   ├── contracts/                     # Pydantic 契约与枚举（第 8 章）
│   ├── store/                         # SQLAlchemy 模型、约束、仓储
│   ├── ingest/                        # adapter、抓取边界、水位编排
│   ├── pipeline/                      # normalize/entities/classify/enrich/runner
│   ├── rank/                          # 分位数、公式、多样性（第 7 章）
│   ├── llm/                           # 通用客户端（openai/anthropic）、配置档案、Gateway（缓存/预算）
│   ├── compose/                       # 冻结、快照、更正、静态导出
│   ├── deliver/                       # 发送账本、飞书、RSS
│   ├── feedback/                      # 匿名身份、事件、当前态、报错
│   ├── web/                           # FastAPI 路由与模板装配
│   ├── ops/                           # CLI、健康检查、备份、成本
│   └── jobs.py                        # 调度任务
├── web/                               # Jinja2 模板与静态资源（360px 移动可读）
├── evals/                             # 评测集目录规范（见 evals/README.md）
├── examples/                          # 人工注入样例
├── tests/                             # 契约/单元/集成/验收测试
└── deploy/                            # Dockerfile、compose、Caddyfile、备份脚本、env 样例
```

## 数据与隐私

- 数据默认写入 `data/`（可用 `VIBERADAR_DATA_DIR` 覆盖）；SQLite 启用 WAL、外键与 busy_timeout。
- `data/llm.json` 存放用户自配的 LLM 档案与 API key（gitignore；Unix 权限收紧为 600，不进数据库与日志）。
- 公开页无私密数据；匿名反馈使用第一方随机标识，服务端带密钥派生 actor，不做指纹补全。
- 收藏仅存于浏览器 localStorage（以 product_id 去重），不上传完整收藏夹、不跨设备同步。
- 源元数据保留 90 天、匿名细粒度事件 90 天、审计与账本 1 年（PRD 10.4）。

## 已知边界（与 PRD 一致）

- 自动化程度：默认自动选刊，人工复核可选；`review` 交互计时用于 ≤15 分钟预算验证。
- 选品采用宽进 soft（`config/pipeline.yaml`）：仅硬拦明确不相关/spam≥0.5/stale/未决实体/30 天重复，其余降级为质量标记；`strict` 恢复 PRD 原全门槛。候选池 60、默认发布 15 条、少于 3 条短刊、上限 30 条。
- 富化“直接试玩”`direct` 需人工/探针实测记录；未实测不承诺 30 秒动手。
- 中文指南在离线 mock 模式下使用确定性模板（可能包含来源原文）；启用任一真实档案后由模型生成。
- 未定价模型（档案未填价格且 pricing.yaml 无该模型）成本记 0 并标注，预算上限只对已定价模型生效。
- PH 接入、RSS 完整反馈闭环、OG 图、Web Console 按 PRD 属于 P1/P2，当前为就绪实现或未启用。
- `eval` 只做数据集完整性与 recall/precision 快检；完整评测报告随样本积累另行发布。

## 下一步

1. 补齐评测样本（`evals/`，≥200 条、含负例与实体对），冻结留出集。
2. 配置 LLM 档案（`viberadar llm set/use` 或 `/admin/llm`，支持 OpenAI 兼容与 Anthropic）、`GITHUB_TOKEN`、飞书测试 webhook 与 `VIBERADAR_SECRET_KEY`。
3. 按 `deploy/env.example` 部署 Web + Scheduler + Caddy，执行一次隔离恢复演练。
4. 执行 E01–E33 场景记录与 7 天连续运行观察，再进入试读（PRD 13.3 DoD）。
