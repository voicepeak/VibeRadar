# 实现说明与验收对照（PRD v1.3）

日期：2026-10-12。代码版本 0.1.0。本文记录 MVP 实现的模块映射、验收场景覆盖情况
与 PRD 13.3 Definition of Done 的当前状态。产品口径以 [PRD](../../PRD.md) 为准。

## 模块映射

| PRD | 实现位置 | 自动化测试 |
|---|---|---|
| M1 采集 | `src/viberadar/ingest/`（hn/github/manual/producthunt、fetch 安全边界、runner 水位） | `test_pipeline_e2e.py::test_source_failure_*`、`test_acceptance_extra.py::test_watermark_*` |
| M2 归一化/实体 | `pipeline/normalize.py`、`pipeline/entities.py`、`store/repo_products.py` | `test_normalize.py`、`test_store.py::test_merge_*`、`test_acceptance_extra.py` |
| M3 分类 | `pipeline/classify.py`、`llm/gateway.py`、`store/repo_pipeline.py` | `test_contracts.py`、`test_pipeline_e2e.py` |
| M4 富化 | `pipeline/enrich.py`（证据、门槛、模板/模型指南、引用完整性） | `test_contracts.py::test_enrichment_*`、e2e |
| M5 排序 | `rank/signals.py`、`rank/formula.py`、`rank/diversity.py`、`pipeline/runner.py` | `test_rank.py`、e2e |
| M5.5 复核 | `ops/review.py`、`store/repo_pipeline.apply_curator_change`、`compose/edition.build_selection` | `test_editions.py`、`test_cli.py` |
| M6 编制分发 | `compose/edition.py`、`compose/render.py`、`deliver/`（账本/飞书/RSS） | `test_delivery.py`、`test_editions.py` |
| M7 反馈 | `feedback/`（actors/events/state/issues）、`web/routes_api.py` | `test_feedback.py`、`test_web.py` |
| M8 存档 | `web/`（模板/路由）、`compose/render.py`（静态导出） | `test_web.py`、端到端冒烟 |
| M9 运维 | `ops/`（cli/health/backup/cost）、`jobs.py` | `test_cli.py`、`test_backup.py`、`test_config.py` |
| LLM 接入 | `llm/`（client/config/gateway/costs）、`web/routes_llm.py`、`ops/cli.py llm` | `test_llm_config.py`、`test_llm_client.py`、`test_llm_gateway.py`、`test_llm_web.py`、`test_llm_compose.py` |
| 第 8 章契约 | `contracts/`、`store/models.py`（唯一键/外键/检查约束） | `test_contracts.py`、`test_store.py` |
| 第 9 章接口 | `web/routes_api.py`（ETag、限流、同源、统一错误格式） | `test_web.py` |
| 第 7 章公式 | `rank/formula.py` 与 `config/rubric.yaml` | `test_rank.py::test_score_formula_*` |

## 运行方式

```powershell
python -m viberadar <命令>          # 或已安装后直接 viberadar
python -m viberadar pipeline --llm none   # 离线规则模式
python -m viberadar serve --port 8000
python -m viberadar scheduler       # 或 scheduler --once（任务计划）
python -m pytest tests -q           # 159 项测试
```

默认 `VIBERADAR_LLM_PROVIDER=auto`：存在 `data/llm.json` 档案时启用真实模型，否则离线 mock
（不调用外部模型；分类走“规则先排、弱匹配等待人工”，指南与开场白走确定性模板）。
档案可用 `viberadar llm set/use/stage` 或 `/admin/llm` 配置窗口维护（OpenAI 兼容与 Anthropic
两种协议），生产按需设置 `VIBERADAR_STRICT_LLM=1`。

## LLM 接入（2026-10-13 扩展）

PRD 10.1 以 DeepSeek 为唯一模型来源；本次扩展在不改变管线契约的前提下把 LLM 层做成可复用组件：

- `llm/client.py`：无数据库依赖的通用客户端，支持 openai（/chat/completions）与 anthropic（/v1/messages）、
  JSON 提取、429/5xx 重试；其他工具可直接 import。
- `llm/config.py`：`data/llm.json` 档案（协议/base_url/密钥/fast+strong 模型/可选价格）与预设；
  密钥仅落本地文件（0600），接口响应一律掩码。
- `llm/gateway.py`：缓存、预算、成本账本、阶段映射（classify/interest→fast，enrich/compose→strong）；
  模型 `price` 缺省时回退 `pricing.yaml`，均未定价则成本记 0 并标注（`price_version=unpriced`）。
- 管线接线：分类/兴趣/指南改用档位名；`freeze_edition` 增加 LLM 开场白（compose prompt），
  `item_ids` 与输入不一致或失败时回退模板；调度任务（cutoff/freeze）按配置自动注入 Gateway。
- 复用出口：`POST /api/llm/chat`（本机直连免令牌、远程需 VIBERADAR_LLM_API_TOKEN，共享缓存与预算）与
  `GET /api/llm/status`；管理面 `GET /admin/llm`（`/settings`、`/admin` 重定向）、
  `GET/PUT /api/admin/llm/config`、`POST /api/admin/llm/test`、`DELETE /api/admin/llm/profiles/{name}`，
  默认开放、不依赖环境变量；Caddy 已把 `/admin/*`、`/api/admin/*` 从公网 404。


## 选品宽进策略（2026-10-13）

默认 `quality_gates.mode=soft`（`config/pipeline.yaml`）：仅「明确不相关 / spam≥0.5 / stale / 未决实体 / 30 天重复且无重要更新」硬拦截；
置信度（<0.8）、兴趣（<0.6）、富化缺失、入口不可达等降级为候选 `quality_flags`，不阻塞入选。冻结层
（`compose/edition.py build_selection`）仅拦截 `evidence_status=failed`，未核查（unverified）放行；strict 模式恢复 PRD 5.3 与 E06 全门槛。

数值：候选池 60、发布目标 15、短刊阈值 3、发布上限 30、深度富化 60；复核池单分类 ≤30、公开单分类占比 ≤0.6。

## 附录 E 验收场景覆盖

| ID | 状态 | 证据 |
|---|---|---|
| E01 正常一期 | ✅ 自动化 | `test_full_pipeline_to_published_edition` |
| E02 短刊 | ✅ 自动化 | `test_freeze_short_and_empty`（2 条 → short，空分组隐藏；宽进 public_min=3） |
| E03 空刊 | ✅ 自动化 | `test_freeze_short_and_empty`、`test_all_sources_failed_produces_empty_edition` |
| E04 单源失败 | ✅ 自动化 | `test_source_failure_does_not_block_other_sources`（partial + 覆盖缺失） |
| E05 全部源失败 | ✅ 自动化 | `test_all_sources_failed_produces_empty_edition` |
| E06 模型不可用 | ✅ 实现 | Gateway 失败 → 规则/模板降级；`evidence_status=failed` 冻结时跳过，未核查放行（soft）；strict 恢复 E06 全证据门槛 |
| E07 免费断言 | ✅ 实现 | `infer_access` 未知默认 unknown；`AccessInfo.display_text`；模板不含无依据免费声明 |
| E08 同名不同产品 | ✅ 自动化 | `test_same_name_different_identity_creates_pending_relation` |
| E09 同产品合并 | ✅ 自动化 | `test_merge_and_undo_restores_links`、`test_store.py` |
| E10 两独立 MCP 仓库 | ✅ 自动化 | `test_different_repos_not_merged`、同名 → 待复核关系 |
| E11 单快照/全相同热度 | ✅ 自动化 | `test_midrank_small_or_constant_falls_back`、`test_velocity_*` |
| E12 部分编辑到截止 | ✅ 自动化 | `test_rejected_candidate_excluded`、`test_curator_edits_applied_to_snapshot` |
| E13 重放 | ✅ 自动化 | `test_replay_dry_run_preserves_published_snapshot`；CLI 默认 dry-run |
| E14 网页成功飞书失败 | ✅ 自动化 | `test_web_delivery_recorded_independently`、`test_feishu_*` |
| E15 超时 unknown | ✅ 自动化 | `test_feishu_timeout_marks_unknown_and_blocks_blind_retry` |
| E16 重复事件 ID | ✅ 自动化 | `test_event_dedup_by_client_event_id` |
| E17 改选/撤回 | ✅ 自动化 | `test_feedback_current_state_flow`、聚合只计最新态 |
| E18 预览机器人 GET | ✅ 实现 | 反馈页 GET 仅渲染；状态写入仅走 PUT API（`test_web.py`） |
| E19 query/fragment | ✅ 自动化 | `test_outbound_link_preserves_query_and_fragment` |
| E20 私网与注入材料 | ✅ 自动化 | `test_manual_injection_rejects_private_urls`、`test_prompt_injection_material_treated_as_untrusted` |
| E21 崩溃恢复水位 | ✅ 自动化 | `test_watermark_advances_only_on_success`；重叠 2 小时去重 |
| E22 快照不被改写 | ✅ 自动化 | `test_historical_snapshot_not_rewritten_by_product_summary`、更正 revision |
| E23 非法配置 | ✅ 自动化 | `tests/test_config.py`（缺文件/非法分类/阈值顺序） |
| E24 隔离恢复 | ✅ 自动化 | `tests/test_backup.py`（哈希校验、篡改拒绝、prune 上限） |
| E25 拒绝虚假召回率 | ✅ 自动化 | `test_eval_refuses_without_dataset`；`eval` 要求完整五件套 |
| E26–E33 | ⏳ 待试读/实测 | 页面、反馈、账本、故障恢复均有自动化覆盖（`test_web.py`/`test_delivery.py`）；两分钟选择、手机实测、复核计时、试读访谈需人工执行并记录 |

## 前端验收（自动化可复跑）

```powershell
python tools/seed_acceptance.py     # 可选：准备昨日常规刊 6 条 + 前天空刊
python tools/qa_frontend.py         # Playwright：桌面 + 360px，44 项断言，截图在 logs/qa/
```

覆盖（对应 PRD 4.6/M8/US-01/02/03/05）：

- 首页分级：重点推荐 → 工具与工作流/创意与实验 → 最近期号入口；短刊/空刊标识；今日未发布时回退展示最近一期并标注日期
- 日期页：重点推荐 1–2 条优先、其余分组；方案（直接试玩/看演示/安装体验）、门槛、证据状态、核查时间可见；往期回顾只含更早期号
- 空刊页：明确"暂无推荐、未用旧产品冒充今日新品"，保留历史入口
- 产品页：中文分类与状态、历史期号列表
- 反馈：卡片评价入口指向反馈页；反馈页保存提示、改选、没玩起来原因选择展开与提交；预览机器人 GET 不产生反馈
- 收藏：localStorage 写入/回显/取消，本浏览器限制提示
- 404：站点版式页（非 JSON）；API 404 统一 `{code,message,request_id,retryable}`
- 360px：无横向溢出、按钮 ≥44px、正文 ≥16px、控制台无 JS 错误

## PRD 13.3 Definition of Done 状态

- [x] 两源真实采集与人工注入可用；PH 适配器条件启用（`sources.yaml` 默认停用）。
- [~] 评测集尚未建立：`evals/` 目录规范与 `eval` 完整性闸门已就绪，样本待补（E25 行为已验证）。
- [x] 留出集保护：`eval` 无全量输入/负例时拒绝输出指标；评测报告格式随样本补充。
- [x] 定时运行：调度器与 `--once` 就绪；连续 7 天真实运行待部署后执行。
- [x] 正常/短刊/空刊/源故障/重放/部分送达/超时场景均有自动化。
- [x] 反馈当前态、改选、撤回、去重、来源与门槛展示已实现。
- [x] 成本账本、日/月上限、配置版本（hash 冻结到 run）、审计日志存在。
- [x] 备份与隔离恢复演练（本地自动化）；异机副本由 `deploy/backup.ps1` 负责，待部署落实。
- [~] 主理人 ≤15 分钟复核：CLI 计时已实现；代表候选计时需实际执行。
- [~] US-01–08 验收记录：US-01/02/03/05 的页面与交互已有 `tools/qa_frontend.py` 自动化证据（44 项断言；"往期不含更新期号" 1 项被核查时间戳误报，待修脚本）；
  E26–E33 中需真人执行的项（两分钟选择、手机实测、复核计时、试读访谈）待试读补充。
- [ ] 试读结果与继续投入决策：属运营动作。

## 已知偏差与边界

1. 离线（mock）模式下指南为确定性模板，可能保留来源原文；启用任一真实档案后由模型生成。
2. Product Hunt 适配器按 PRD 保持停用，需凭据与条款核查后开启。
3. 限流为单实例内存实现，多 worker 部署需替换为外部存储（P0 为单实例）。
4. `review` 交互为终端问答式（支持 j/k/a/r/e/l/u/s 键位），非全屏 TUI；满足 P0 CLI 要求。
5. `eval` 提供数据集完整性与 recall/precision 快检，不是完整评测报告；门槛比较结果随样本版本记录。
6. 未定价模型成本记 0（标注 `unpriced`），预算上限只覆盖已定价模型；可在档案或 `pricing.yaml` 补价。
7. `/api/llm/chat` 为内部工具面：本机直连免令牌，远程需要令牌；公网部署保持管理路径 404（Caddy 默认）。
