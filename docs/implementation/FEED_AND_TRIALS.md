# 滚动 Feed 与本地试玩实现记录

日期：2026-10-14。对应 [#83](https://github.com/voicepeak/VibeRadar/issues/83) 与
[#84](https://github.com/voicepeak/VibeRadar/issues/84)。本记录优先于旧文档中“首页展示最新一期”的描述。

## 首页与刷新

`/`、`/home` 显示产品维度的滚动 Feed：重要必看、近期火爆、最新发现、全部动态。
同一产品在数据库中只有一行，页面按必看、火爆、最新、全部的顺序去重；
全部动态每页 20 条。日期页、冻结、更正、公开流程和 RSS 保留为存档线。

新增 `feed_items`、`refresh_runs`，由 `init_db/create_all` 创建，旧表不修改。
首次刷新会发现已有采集记录，并复用已有富化/卡片。无新增产品且无 pending 队列时不调用模型，
只更新确定性排名；卡片内容保持不变。不同来源提及同一产品会增加来源关系和热度快照，
不会再次富化或覆盖卡片。证据按来源 URL 与摘录 hash 复用。

刷新账本记录触发来源、窗口、新 raw、新卡片、模型请求数、预算状态、开始和结束时间。
锁为数据库租约，10 分钟；逐源和逐产品更新租约。锁在网络 I/O 前提交，
采集/模型请求期间释放 SQLite 写事务，读者可以查询运行状态。冷却为 3 分钟，所有入口共用。
模型请求数包括 Gateway 网络重试，不计缓存命中；离线模式用确定性模板。
每轮最多深富化 40 条、强模型 12 条；余下 pending。预算耗尽继续采集重排，分析留到下一轮。

栏目使用已有评分公式和源内分位数：必看最多 3 条，需高分、证据和兴趣达标，粘性 12 小时；
火爆为 72 小时内、增速分位数 ≥0.8 且热度分位数 ≥0.6，最多 5 条；最新为 48 小时内；
7 天后归档。热度增速可超过每小时 1，因此先保留原始测量值，再计算 0–1 分位数。
时间存储 UTC，展示 Asia/Shanghai。每轮另按最旧快照优先，最多复采 12 个仍在 72h 窗口内的 HN/GitHub 产品，
即使热度未变化也记录时点，让停止增长的产品能退出火爆榜；火爆的热度与增速必须在同一源同时达标。

```powershell
python -m viberadar feed refresh --json
python -m viberadar feed refresh --no-llm --json  # 只采集与重排；新产品保留 pending
python -m viberadar feed status
python -m viberadar scheduler                   # Feed 每 20 分钟；原存档任务仍保留
```

`POST /api/v1/refresh` 返回 202 + `run_id`；冷却返回 `status=cooldown,next_at`。
`GET /api/v1/refresh/{id}` 查询计数和状态。
`GET /api/v1/feed?section=&cursor=&lang=` 返回卡片 JSON、下一页游标和与 SSR 共用的 HTML 片段。
`POST /refresh` 提供无 JS 的 303 回首页路径。公开刷新受同源检查和 IP 限流保护。
无新内容不会发飞书；刷新账本固定 `delivery=pending(TODO feishu)`，UI 显示“飞书：未实现”。
TODO：另行实现 Feed 飞书发送；本轮不调用 webhook。既有日报飞书路径维持原逻辑。

## 本地试玩

新增 `TryTier/TryRecipe/TrialSession` 契约，以及 `try_recipes/trial_sessions/trial_events`。
配方版本不可覆盖，重新核验应使用新版本。只读标签在跳转前显示；核验缺失、许可未核查或
连续失败 3 次时降级为 guided。不会让模型在启动时生成安装命令。

```powershell
python -m viberadar recipe import --file config/recipes/open-webui.yaml
python -m viberadar recipe list
# 将配方写入新版本，完成安装/网络/许可人工核验后记录核验人和设备：
python -m viberadar recipe verify --file examples/recipes/local-python.yaml --by curator --device win11-docker
python -m viberadar try local-python
python -m viberadar try status
python -m viberadar try stop --session trl_...
python -m viberadar try clean       # 清理终止/过期会话
python -m viberadar try clean --all
```

CLI 试玩需同时运行本机 Web 服务；涉及模型的工具通过该服务代理。
默认模型代理端口来自 `VIBERADAR_SITE_URL`；使用其他端口时设置相同的 URL。
网页 `/try/{slug}` 展示门槛、实测时间（没有样本则明确未验证）和 Docker 可用状态；
`/trial/{id}` 展示日志、阶段、已用时间、入口与停止按钮。日志可 SSE 读取、可复制，
无 JS 可通过重新加载查询。启动/停止只允许本机客户端且校验 Origin；不提供匿名托管运行。
Web 和 scheduler 均有定期 TTL 回收，数据库状态允许重启后发现过期会话。

工作区在 `VIBERADAR_DATA_DIR/trials/{id}/`，基础镜像只读、UID/GID 1000、
CPU 1 核、内存 512 MiB、PID 128、cap-drop=ALL、no-new-privileges；仅挂载自己的工作区，
没有 Docker socket。预置镜像用声明的 shell 命令运行。源代码配方需要完整 40 位提交 SHA，
从 GitHub 下载压缩包；拒绝路径穿越、符号链接、特殊文件和过大归档。
compose 级使用同一内部网络上的最多 4 个受同样约束的声明式服务，
不接受任意带宿主挂载或特权配置的第三方 Compose YAML。

试玩容器只接入 Docker isolated internal 网络（需要支持 isolated 网关模式的 Engine 28+）。
relay 的 loopback 入口固定转发到会话应用；应用自身不发布端口。
独立 relay 在安装期只允许精确声明的包源域名，
禁止私网 DNS 结果；进入运行期后包源白名单清空，只开放固定会话模型代理路径。
relay 不持有真实模型密钥，沙箱仅获得可吊销虚拟密钥。停止、失败、过期时撤销密钥。
会话文件不进备份，数据库中的配方/审计元数据保留。

代理提供 OpenAI `/chat/completions` 和 Anthropic `/messages` 文本会话协议，
可转换协议，保留多轮文字上下文；可返回兼容 SSE，但上游响应先完成预算核算再输出。
当前不支持工具调用和多模态。真实 key 仅在宿主 Gateway/客户端加入上游认证头。
会话和全局账本先原子预留成本，再结算实际用量，防止并行请求越过会话/全局预算；
上游结果不明时保留带 estimated 标记的预留，拒绝静默透支。未定价模型拒绝试用代理。
来源模型名称不能由试用应用任意指定。日/月预算仍适用。

“玩过了”需读者手动确认且 ready 至少持续 30 秒；启动成功不自动生成 tried。
`trial_events` 独立保存会话证据，反馈 PUT 可选 `trial_session_id`，校验产品、actor 与 ready 时间；
旧的匿名自报反馈统计保持原口径。`status` 和 `recipe list` 展示成功率、中位启动时间和复核状态。
试玩难易不会增加排序分。

## 验证与尚未完成的现场验收

`tests/test_feed.py` 覆盖零增量、卡片不覆盖、原始消息去重、冷却/并发、预算短路、pending 续跑、
高于 1/h 的真实增速、12h 粘性、栏目去重、分页、API/SSR 与双语。
`tests/test_trial.py` 使用假 Docker 引擎覆盖配方冻结、命令约束、失败降级、TTL、
代理协议、预算、吊销、日志、同源和本机访问；这些测试不等同于真实容器隔离验收。

`tools/qa_frontend.py` 已扩展为 Feed 页面，2026-10-14 验收修复后全量 pytest 为 208/208 通过；
浏览器在隔离示例库上常规检查 52/52、补充检查 10/10 通过，
覆盖桌面/360px、无 JS、双语、展开指南、收藏、反馈、归档、404、异步刷新、冷却和未核验试玩。
完整结论与测试边界见 [2026-10-14 验收报告](ACCEPTANCE_2026-10-14.md)。
`tools/preview_feed.py` 仅在 `.local/qa-feed/` 写入虚构 UI 测试数据，端口 8011。
`tools/qa_try_runtime.py` 提供真实 Docker 的第一方启动/网络/回收探针。

初次验收没有 Docker；后续已安装 Docker Desktop，第一方探针的资源约束、外网阻断、
宿主阻断（带 relay 阳性对照）、包源禁用及回收通过真实测试。
MarkItDown 基础 HTML 转换首次 96.249 秒、再次 83.806 秒，回归测试 217/217 通过。
追加预装镜像原型三次就绪 4.615 / 3.010 / 3.190 秒，成功准备 50.694 秒，回归测试 222/222 通过。
该镜像尚未公开发行；用户首次下载时间未测量。快速试玩候选建议见 [准入建议](../product/FAST_TRIALS.md)。
详见 [真实计时报告](TRIAL_TIMING_2026-10-14.md)。
追加完整链路：固定 MarkItDown 发现输入，经用户配置的真实 DeepSeek Flash 刷新生成新卡片，再自动导入已核验本地镜像包，实际粘贴/上传 HTML 并下载转换结果，确认玩过及停止回收均通过。
刷新 16.308 秒；镜像缺失时点击启动至体验入口 14.650 秒（包已在本机，不含下载或 Docker 安装）。最新回归 232/232 通过。原始证据与范围见 [本地完整链路报告](LOCAL_CHAIN_2026-10-14.md)。
#84 的“五个精选配方在干净机器跑通”和全面真实红队检查仍未完成。
`config/recipes/` 的 5 个候选故意维持 guided，`verified=null`；它们不是已核验的一键配方。
P2C 托管沙箱仍按提案等待需求验证，未在本次开放。

配方可选声明 `image_archive_sha256`，此时 `image` 必须为完整 `sha256:<64 hex>` 内容 ID。
镜像缺失时仅从 `data/recipe-images/<archive_sha256>.tar.gz` 导入，校验包哈希、512 MiB 大小上限及导入后的镜像 ID；不自动下载任意包或执行未知项目安装脚本。
Windows 已安装但停止的 Docker Desktop 可在本机同源启动请求后唤起；未安装时仍显示安装门槛。
