# Vibe Radar · 产品需求文档（PRD）

| 项目 | 内容 |
|---|---|
| 产品代号 | Vibe Radar（中文暂名「振感雷达」，正式名待定） |
| 文档版本 | v1.0 |
| 日期 | 2026-10-11 |
| 状态 | 待评审 |
| 实现方式 | Agent 协同开发（本文档含模块接口、验收标准、评测金样例） |

> **阅读指引**：第 1–3 章建立共识；第 4–8 章是实现规格（按模块拆给 agent）；第 9 章是里程碑与验收；附录 A 是质量基准（金样例），附录 B 是信源 API 手册，附录 D 是 Prompt 模板。开发原则：**接口先行、配置驱动、模块独立可测、金样例回归**。

---

## 1. 背景与机会

### 1.1 问题

vibe coding 产品正在爆发式增长，但：

1. **发现成本高**：新品散落在 HN、Product Hunt、GitHub、X、Reddit、即刻等十几个平台，没有统一入口。
2. **筛选成本更高**：每天数百个候选，90% 是噪音；社区分数（upvote/star）与「有趣程度」几乎不相关。
3. **上手成本劝退**：看到一个产品后，用户还要自己读官网、注册、摸索——「打开就玩到」的体验几乎不存在。
4. **品味无法积累**：没有产品帮助用户系统性地接触「新的、有趣的」东西，品味提升靠运气。

### 1.2 实证发现（2026-10-09 / 10-10 两天人工探测结论）

这是本文档所有产品决策的第一手依据：

| # | 发现 | 对产品的要求 |
|---|---|---|
| F1 | 用主题词检索无效（GitHub 搜 `vibe` 470+ 结果，好货仅 4–5 个）；检索**发布行为**（Show HN / PH 新榜 / 当日新仓库）才有效 | 采集层按「发布事件」建模，不按关键词爬全站 |
| F2 | 每个源拥有独占好货：Hypervibe 只在 PH、Iimos 只在 HN、dudes 只在 GitHub | 必须多源聚合，单源产品天花板低 |
| F3 | 分数不预测有趣度：Iimos（HN 2 分）比 Nagare（15 分）有趣；10/9 HN 最高分是「屏幕上画大箭头」（406 分） | 排序层不能直接用社区分，需要独立的「趣味度」信号 |
| F4 | 日与日量级差 3–5 倍（PH 冠军 354 票 vs 71 票） | 每日打分要**当日归一化**，避免读者被绝对值误导 |
| F5 | 跨天有模式信号：麦当劳 MCP 连续两天出现；「agent 卫生」品类单日冒出 4 个（noskip / Context Diet / Memdebug / Mudroom） | 需要「跨天实体追踪」与「新品类探测」模块 |
| F6 | 中英文是两套生态：中文项目几乎只出现在 GitHub，PH/HN 无覆盖 | 采集必须含中文源，不能只做英文 |
| F7 | 「给 agent 善后」是真痛点品类（防作弊、上下文管理、沙箱、记忆回滚） | taxonomy 需要独立的 hygiene 分类，权重倾斜 |

### 1.3 竞争格局（2026-10 调研）

| 产品 | 形态 | 内容 | 缺口 |
|---|---|---|---|
| The Vibe Coding Report | 每日 one-liner | 行业新闻扫描（融资/模型/安全，来源=新闻 RSS） | 不是新品发现；无筛选、无试玩、无草根源 |
| VibeCoders | 周报 | Reddit/X/YT/Medium 社区声音聚合 | 偏文化；周更；列老面孔 |
| PH 每日榜 / HN / GitHub Trending | 平台原生 | 有新品但单一源、无跨源去重、无试玩指南 | 只是原料，不是产品 |
| Vibe Coding Academy / vibecoding.study 等 | 周更 newsletter | 教学与经验分享 | 非产品发现 |
| 中文生态 | — | 知乎/头条对比文、掘金教程帖 | 无持续运营的日更产品 |

**结论：邻近者众，「每日新品发现 + 30 秒试玩 + 品味反馈」的组合无人占位。**

### 1.4 差异化定位

> 别人做「新闻」，我们做「可玩性」；别人做「目录」，我们做「时间线」；别人做「榜单」，我们做「品味」。

护城河（按强度排序）：
1. **品味的定义与执行**（rubric + 主理人反馈数据）
2. **跨源去重与实体追踪的工程积累**
3. **用户试玩反馈数据集**（谁都能抄功能，抄不了数据）
4. 供应链自动化的日更稳定性

---

## 2. 产品定义

### 2.1 一句话价值主张

> **每天早上 9 点，给你一份「过去 24 小时新冒出来的 vibe coding 产品」清单——每一条都告诉你：这是什么、为什么有趣、以及 30 秒内怎么玩到它的 wow 点。**

### 2.2 目标用户

| 优先级 | 画像 | 核心诉求 | 典型场景 |
|---|---|---|---|
| P0 | Vibe coder / AI builder 爱好者 | 跟上新品潮流、提升品味、获得灵感 | 早晨通勤刷日报，挑 1–2 个试玩 |
| P1 | 独立开发者 / 创业者 | 找工具、找需求空隙、看竞品 | 需要时检索存档，或订阅周报 |
| P2 | 产品经理 / 投资人 | 看生态趋势与新品信号 | 周维度查看趋势报告 |
| P3 | 内容创作者 | 找选题素材 | 引用日报内容做视频/文章 |

### 2.3 核心场景（User Journey 摘要）

```
08:55  读者收到飞书卡片 / 打开存档站
09:00  扫一眼：3–5 秒判断「今天有没有值得玩的」
09:01  点开 Top1 的 30 秒试玩指南 → 照做 → 感受到 wow 或无聊
09:03  点击「玩过了」/「无聊」按钮（一次点击，无需登录）
        ↓ 系统记录，反哺排序
周末   存档站看「本周趋势：agent 卫生品类在爆发」
```

### 2.4 非目标（Non-goals，明确不做）

- ❌ 不做全量工具目录（不追求「收录所有工具」）
- ❌ 不做深度评测/打分（只回答「有趣吗 / 怎么玩」）
- ❌ 不做新闻资讯站（融资、模型发布等不属于本产品）
- ❌ v1 不做用户账号体系（匿名事件追踪即可）
- ❌ v1 不做个性化推荐（先定义公共品味）

### 2.5 收录标准：什么是「vibe coding 产品」

用**判定问卷**定义（分类器 prompt 依据，见附录 D-1），满足以下任一：

1. 用于**构建/辅助构建**软件产品的 AI 工具（agent、builder、IDE 插件、harness）
2. 服务于**vibe coding 工作流**的工具（上下文管理、测试防作弊、沙箱、配额监控、会话管理、MCP server/skill）
3. **由 vibe coding 方式构建**的、且本身有趣的产品（自述 "vibe-coded"、离谱创意作品、community 实验）
4. 与上述生态强相关的**支撑设施**（模型路由、agent 编排、eval）

排除：纯模型发布、纯融资新闻、通用 AI 聊天、与开发无关的 AI 应用（除非极有趣，走人类复核通道）。

### 2.6 分类体系（Taxonomy v1）

```
1. coding-agent        编码 agent / harness / 多 agent 编排
2. agent-hygiene       给 agent 善后：防作弊、上下文、记忆、沙箱、配额、调试
3. app-builder         prompt-to-app / 网站构建器 / UI 生成
4. editor-extension    编辑器/IDE 插件、会话管理、辅助工具
5. mcp-skill           MCP server、agent skill、协议工具
6. infra-model         模型路由、推理、eval、基础设施
7. creative-toy        趣味优先、无用但完美的作品（如麦当劳 MCP）
8. meta                生态元话题：工具链盘点、技术方案、趋势数据
```

---

## 3. 角色与旅程（Operational Model）

### 3.1 三种角色

| 角色 | 描述 | 关键动作 |
|---|---|---|
| 读者 Reader | 日报消费者 | 阅读、点击、试玩反馈 |
| 主理人 Curator | 品味定义者（= 项目所有者，初期每天投入 ≤ 15 分钟） | 每日 review 候选、修订 rubric、撰写编者按 |
| 系统 System | 自动化管线 | 采集→处理→排序→生成→推送 |

### 3.2 主理人每日操作（关键：15 分钟以内）

```
08:00  系统完成管线，产出 20 条候选的 Review 面板
08:05  主理人打开面板：快速过一遍（预计 5–8 分钟）
       - 通过 / 淘汰 / 编辑文案 / 调整排序
08:13  点「发布」→ 系统生成日报并推送
08:15  离线：系统记录所有编辑动作作为品味训练数据
```

### 3.3 发布策略

- 默认**全自动发布**（无人工是降级路径，保证日更不断）
- 主理人在线时走 review 模式；离线时走「autopilot 模式」（prompt 要求见附录 D-4）
- 两种模式质量差是品味系统的重要训练对

---

## 4. 功能需求（M1–M9 模块规格）

### 4.0 模块总览

```
┌─────────┐   ┌──────────┐   ┌──────────┐   ┌─────────┐   ┌──────────┐
│ M1 采集  │ → │ M2 归一化 │ → │ M3 分类  │ → │ M4 富化  │ → │ M5 排序  │
│ Ingest  │   │ & 去重    │   │ Classify │   │ Enrich   │   │ Rank     │
└─────────┘   └──────────┘   └──────────┘   └─────────┘   └────┬─────┘
                                                                ↓
┌─────────┐   ┌──────────┐   ┌──────────┐   ┌──────────┐   ┌──────────┐
│ M9 运营  │ ← │ M8 存档  │ ← │ M7 反馈  │ ← │ M6 发布  │ ← │ M5.5 复核 │
│ Console │   │ Archive  │   │ Feedback │   │ Compose  │   │ (主理人) │
└─────────┘   └──────────┘   └──────────┘   └──────────┘   └──────────┘
```

调度：全部通过一个每日 Pipeline Job 串联，各模块产出落库，中间产物可重放（replay）以支持调试与评测。

---

### M1 · 采集（Ingest）

**目标**：每天 08:30（北京时间）前，完成所有信源的滚动 24 小时采集，原始数据落库。

**时间窗口定义**：滑动 24h（`now - 24h → now`），而非自然日，避免时区与源更新节奏问题。

**信源分级**：

| 级别 | 信源 | 接入方式 | 频率 | 备注 |
|---|---|---|---|---|
| S1 | Hacker News | Algolia API（免费） | 每 4h | `tags=show_hn` + `query=vibe/coding agent` 等 |
| S1 | GitHub | Search API（需 token） | 每 4h | `created:>窗口 + topic:ai / keyword` |
| S1 | Product Hunt | GraphQL API v2（需 token） | 每日 1–2 次 | 当日 leaderboard |
| S2 | Bluesky | AT Protocol 公开 API | 每 6h | `app.bsky.feed.searchPosts` |
| S2 | Reddit | OAuth（script app） | 每日 | r/SideProject、r/artificial、r/LocalLLaMA 的新帖 |
| S2 | Hugging Face | 公开 API | 每日 | trending spaces / models |
| S3 | 中文源（即刻/V2EX/少数派） | RSSHub 自建桥接 | 每 6h | 需自部署 RSSHub |
| S3 | Newsletter（TLDR/Ben's Bites 等） | 邮件/RSS 解析 | 每日 | 作为「发现者信号」而非直接收录 |
| S4 | 人工注入 | Console 手动提交 | 随时 | X/Twitter 等难以自动化的源 |
| S4 | 用户提交 | 存档站表单 | 随时 | 需防滥用（rate limit + 审核） |

**接口契约**：

```python
class RawItem(BaseModel):
    source: str            # "hn" | "github" | "ph" | "bluesky" | ...
    external_id: str       # 源内唯一 ID
    title: str
    url: str | None
    description: str | None
    author: str | None
    metrics: dict          # {"points": 15, "comments": 21, "stars": 5, ...}
    published_at: datetime # UTC
    fetched_at: datetime   # UTC
    raw: dict              # 原始 payload 存档
```

**验收标准（AC）**：
- [ ] 新增一个信源 = 新增一个 adapter 文件 + sources.yaml 一行配置，不改核心代码
- [ ] 任一源失败不影响其他源（隔离 + 重试 3 次 + 指数退避）
- [ ] 所有请求遵守 robots / API 速率限制；仅存元数据与链接，不复制全文
- [ ] 采集覆盖率可观测：每源每日产出量有 dashboard 记录

---

### M2 · 归一化与去重（Normalize & Dedup）

**目标**：跨源识别「同一个产品」，合并为唯一 `Product` 实体。

**流程**：
1. URL 归一化（去 utm、统一 www/https、提取根域名）
2. 名称归一化（小写、去空格、去 "Show HN:" 前缀等）
3. 规则去重：同域名 OR 名称完全匹配
4. 模糊去重：名称编辑距离 + 描述 embedding 相似度（阈值可配置，默认 0.88）
5. 合并策略：保留最早 `first_seen`，sources 数组聚合，metrics 合并

**验收标准**：
- [ ] 同产品出现在 HN + PH + GitHub（如 busabase 类案例）时正确合并为 1 条
- [ ] 不同产品共用一个域名（如 vercel 下多个产品）不被误合并 → 名称模糊层需可人工校正
- [ ] 提供 `merged_from` 审计字段，可回滚误合并
- [ ] 金样例回归：用附录 A 两天数据回放，重复率 < 2%（人工抽查）

---

### M3 · 分类判定（Classify）

**目标**：判断候选是否属于收录范围（PRD 2.5），并归入 taxonomy（2.6），同时打 spam 分。

**实现**：LLM 批量分类（cheap model，见附录 D-1 prompt），每批 20–50 条；输出 JSON：

```python
class Classification(BaseModel):
    relevant: bool
    category: str          # taxonomy key
    confidence: float      # 0-1
    spam_score: float      # 0-1（自我推广、刷票、AI slop 特征）
    reason: str            # 一句话理由（供运营复核）
```

**spam 信号参考**：新注册账号 + 自我外链；描述空洞词堆砌（"revolutionary"、"10x"）；同域名当日重复提交；PH 短时间异常票数曲线。

**验收标准**：
- [ ] 金样例 136 条（两天）召回率 ≥ 90%，误报率 ≤ 15%（人工复核为基准）
- [ ] 分类错误集中在可接受的 category 混淆（如 infra-model ↔ mcp-skill），不出现 relevant 误判大类
- [ ] 单条成本 < $0.001，全管线日成本 < $1（分类部分）

---

### M4 · 富化与试玩指南（Enrich & Try-Guide）

**目标**：把一条候选变成「可玩卡片」所需的全部素材。

**富化内容**：
1. 抓取产品页 / README（标题、tagline、截图 URL 如有）
2. 生成字段：
   - `one_liner`：一句话卖点（≤ 40 字，中文）
   - `why_interesting`：为什么有趣（指向 rubric 维度，1-2 句）
   - `try_guide`：30 秒试玩指南
   - `access`：`free` / `login` / `paid` / `waitlist` / `self-host`
   - `tech`：语言/技术栈（如有）
3. （Phase 2）Playwright 无头浏览器截图 + 首屏加载时间探测

**30 秒试玩指南结构**（模板见附录 D-3）：
```
① 打开：{url}（{access 说明}）
② 第一步：{具体动作，如"输入任意一句话" }
③ 看什么：{wow 点，如"它会实时重写自己的界面"}
④ 坑：{如需登录/仅限 Mac/需要 API key}
```

**验收标准**：
- [ ] 每条指南可被一个不了解该产品的人「照做」并在 60 秒内触达核心体验
- [ ] 禁止编造功能：指南中每个断言必须能在产品页/README 找到依据（自动化检查：claim → 来源句）
- [ ] `access` 字段准确率 100%（人工抽查：错误的试玩成本最高）

---

### M5 · 打分与排序（Rank & Taste）

**目标**：从 40–80 条相关候选中，产出有序的 Top 20 给主理人、Top 8–15 给读者。

**打分公式（v1，全部信号先当日归一化）：**

```
final_score = 0.20 * source_signal     # 源权重 × 当日 z-score（points/stars 快照，冷却 24h）
            + 0.15 * velocity          # 单位时间热度增速（log 压缩）
            + 0.25 * novelty           # 新鲜度衰减：exp(-hours_since / 18)
            + 0.25 * llm_interest      # LLM 趣味度评分 0-10（附录 D-2 prompt）
            + 0.15 * taste_match       # 与品味向量的匹配（v1 = rubric 规则分，v2 = 学习模型）
            - 0.30 * spam_score        # 直接扣减
```

**关键设计点（来自实证发现）**：
- **低分保护**：`source_signal` 只有 0.20 权重，防止高分平庸货霸榜（F3）
- **当日归一化**：源内 z-score，避免 354 票 vs 71 票的日子被绝对值误导（F4）
- **llm_interest 独立**：LLM 依据 rubric 打分，与社区分数解耦
- **品类加权**：`agent-hygiene` 与 `creative-toy` 类目加权 1.15（生态痛点与趣味保护，F5/F7）
- 多样性约束：同一 category 最多占 Top 20 的 40%，Top 8 至少覆盖 3 个分类

**验收标准**：
- [ ] 回放两天金样例：系统 Top 10 与人工精选重合度 ≥ 60%（v1 目标，v2 提升到 75%）
- [ ] 反例测试：把 Nagare（15 分平庸）与 Iimos（2 分有趣）输入，Iimos 排名更高
- [ ] 打分过程可解释：每条候选输出信号明细（供 Console 展示）

---

### M5.5 · 主理人复核（Curator Review）

**目标**：15 分钟内完成人工把关，编辑动作自动沉淀为训练数据。

**Console 功能**：
- 候选列表（按 final_score 排序，显示信号明细、分类、spam 分、理由）
- 操作：通过 / 淘汰（选原因：不相关/太无聊/重复/低质）/ 编辑文案 / 拖拽调序
- 一键发布 / 转 autopilot
- 每一条操作写入 `curator_actions` 表（训练数据）

**验收标准**：
- [ ] 全流程键盘可操作（j/k 移动、x 淘汰、↵ 通过），20 条候选 8 分钟内可完成
- [ ] autopilot 模式与 review 模式发布结果 diff 可查询（训练对来源）

---

### M6 · 编制与发布（Compose & Deliver）

**目标**：生成日报并多点分发。

**日报版式（v1，基于金样例提炼）**：
```
📡 Vibe Radar · 10月9日

今日 12 条新货，为你精选 6 条

🔥 AI 编码 / Agent
▸ Busabase（PH #1，354票）
  agents 的公共系统记录：人类和 Claude Code 共用的数据库
  ▸ 30秒试玩：打开 busabase.com → 创建 workspace → 让 Claude Code 连接
  [玩过了] [收藏] [无聊]

🎨 趣味 / 创意
▸ Taipei Rush — GTA 式 vibe coding 游戏，台湾爆火中
  ...

📊 今日信号
· 「Jev」梗刷屏：HN 冒出 6+ 决策模型互相 callout
· 麦当劳 MCP 连续两天出现（生态趋势）
```

**渠道**：
| 渠道 | 优先级 | 说明 |
|---|---|---|
| 飞书机器人卡片 | P0 | 交互按钮直接回流反馈；首发渠道 |
| 存档站（Web） | P0 | 每条日报一个永久链接；SEO/分享入口 |
| RSS | P1 | 零成本分发给高级用户 |
| 邮件 | P1 | Phase 2（Resend/Postmark） |
| Discord / Telegram | P2 | 海外用户 |

**飞书卡片按钮设计**：`玩过了` / `收藏` / `无聊`（三个按钮都不需要打开网页，点击即回流事件）。

**验收标准**：
- [ ] 08:45 前生成完毕，09:00 准点推送（管道失败时有降级：推送「今日简报生成中」+ 事后补发）
- [ ] 每条外链带 `?ref=vibradar&item_id=xxx` 用于点击追踪
- [ ] 存档站每条日报 LCP < 2s，移动端可读

---

### M7 · 反馈闭环（Feedback & Learn）

**目标**：收集「真正试玩了什么」，反哺排序。

**事件模型**：

| 信号 | 类型 | 权重 |
|---|---|---|
| 日报打开 | 隐式 | 0.1 |
| 外链点击 | 半显式 | 0.5 |
| 「玩过了」 | 显式 | 1.0 |
| 「收藏」 | 显式 | 0.8 |
| 评分（1–5，可选展开） | 显式 | 1.0 |
| 「无聊」 | 负显式 | -0.6 |
| 停留时长（存档页） | 隐式 | 0.2 |

**反哺路径**：
1. 短期：`taste_match` 特征更新（同 category/同技术栈加权）
2. 中期：每周生成「品味报告」给主理人（哪些品类命中率高），人工确认后调整 rubric 权重
3. 长期：积累 (item 特征, 用户反馈) 数据集 → 训 LTR 模型（v3）

**验收标准**：
- [ ] 匿名设备级追踪（UUID cookie），无需登录
- [ ] 事件 5 分钟内可在 Console 查询
- [ ] 周报自动生成：Top/Bottom 品类、反馈率趋势

---

### M8 · 存档站（Archive & Share）

**目标**：日报的永久归档与分享载体。

**页面**：
- `/` 今日日报（未发布时显示昨日）
- `/d/2026-10-09` 按日归档
- `/p/busabase` 产品页（聚合该产品的所有日报出现记录）
- `/trends` 周/月趋势（Phase 3：新品类探测、跨天实体追踪——「麦当劳 MCP 现象」）

**SEO**：`/d/:date` 与 `/p/:slug` 是天然 SEO 页（「vibe coding 新品 2026-10-09」这类查询无竞争）。

**验收标准**：
- [ ] 静态生成（SSG）优先，页面可被搜索引擎索引（sitemap.xml + OG 卡片）
- [ ] 分享卡片（OG image）自动生成：日期 + 当日 Top 3 名称

---

### M9 · 运营后台（Ops Console）

**目标**：系统健康的可视性与人工干预能力。

**功能**：
- Pipeline 状态（每源采集量/成功率/耗时）
- 候选池管理（手动注入 S4 信源、撤销合并、拉黑域名）
- rubic 管理（YAML 在线编辑 + 版本历史）
- 金样例回归测试一键运行
- 成本面板（LLM token、API 配额）

**验收标准**：
- [ ] 任何一天失败可从 Console 一键重放该日 pipeline
- [ ] sources.yaml / rubric.yaml 修改不需要发版（热加载）

---

## 5. 数据模型

```sql
-- 信源配置（可由 yaml 同步）
sources(id, key, name, tier, enabled, config_json, updated_at)

-- 原始条目
raw_items(id, source_key, external_id, title, url, description, author,
          metrics_json, published_at, fetched_at, raw_json,
          UNIQUE(source_key, external_id))

-- 产品实体（去重后）
products(id, slug, name, domain, one_liner, description,
         first_seen_at, launch_date, category, access,
         try_guide, why_interesting, screenshot_url, tech,
         status,  -- candidate | published | archived | blocked
         spam_score, created_at, updated_at)

-- 产品↔来源（多对多）
product_sources(product_id, raw_item_id, source_key, merged_from_json)

-- 每日期号
editions(id, date, status,  -- draft | reviewing | published | failed
         published_at, autopilot, curator_id)

-- 期号内容（最终上榜）
edition_items(edition_id, product_id, position, section, final_score,
              signals_json, curator_note)

-- 反馈事件
events(id, device_id, user_id, edition_id, product_id, event_type,
       value, ref, created_at, meta_json)

-- 主理人动作（训练数据）
curator_actions(id, edition_id, product_id, action,  -- approve | reject | edit | reorder
                reason, diff_json, created_at)

-- 品味配置版本
rubric_versions(id, version, yaml, changelog, created_at)

-- 索引
CREATE INDEX idx_products_status ON products(status, launch_date);
CREATE INDEX idx_events_edition ON events(edition_id, product_id);
CREATE INDEX idx_raw_window ON raw_items(source_key, published_at);
```

---

## 6. 品味系统（核心壁垒）

### 6.1 评分 Rubric（每条候选，LLM 依据此打分）

| 维度 | 问题 | 分数锚点 |
|---|---|---|
| 创意度 | 我（经常刷产品的人）见过这个东西吗？ | 5=完全没见过 3=老概念新组合 1=又一個 |
| 可玩性 | 30 秒内能触达 wow 吗？要不要重配置？ | 5=打开即玩 3=需要注册 1=要装环境 |
| 完成度 | 核心流程能跑通吗？还是纯壳？ | 5=可上手用 3=demo 级 1=纯概念 |
| 反直觉 | 有没有「竟然可以这样」的意外感？ | 5=震惊 3=会心一笑 1=预期内 |
| 生态位 | 它填补了什么空缺？（对照 F7 类痛点） | 5=解决公认痛点 3=锦上添花 1=重复造轮子 |

### 6.2 冷启动策略（从两天金样例提炼的启发式）

1. **低分高创意保护**：HN points < 5 且描述含「自指/反常识/重写自身/实验」特征 → `llm_interest` 加成
2. **品类空白优先**：当日首次出现的 taxonomy 子类（如 agent-hygiene 首日）加权
3. **无用之美保护**：`creative-toy` 类（麦当劳 MCP 型作品）不因「没有商业价值」降权
4. **完成度门槛**：无可访问链接/仓库的条目最多进候选池，不进日报
5. **跨源验证加成**：同一产品被 2+ 个独立源提及 → +10% 分

### 6.3 反馈信号权重

见 M7 事件表。核心原则：**「玩过了」>「点击」>「打开」**；「无聊」是强负信号，比不点击更值得学习。

### 6.4 学习路径

| 版本 | 方法 | 数据需求 |
|---|---|---|
| v1 | 规则加权 + LLM 打分（本文档公式） | 0（冷启动） |
| v2 | 逻辑回归 / LambdaMART（特征 = 信号 + category + source） | ≥ 500 条已标注（主理人动作 + 用户反馈） |
| v3 | 个性化：用户向量 × 条目向量（协同过滤 + embedding） | ≥ 1000 活跃用户 |

### 6.5 反噪声

- 自我推广检测（spam_score，见 M3）
- AI slop 检测：描述模板化、无实际 demo、域名注册 < 7 天
- 域名黑名单机制（Console 可维护）
- 「刷票」异常检测：PH 票数曲线斜率异常

---

## 7. 技术架构

### 7.1 总体架构

```
[Cron/APScheduler] → [Pipeline Orchestrator]
                          │
   ┌──────────────────────┼──────────────────────┐
   ▼                      ▼                      ▼
[Ingest adapters]   [LLM Service]         [Storage: SQLite→PG]
   │                (classify/compose)          │
   ▼                      │                     ▼
[Normalize/Dedup] → [Rank/Taste] → [Compose] → [Deliver: 飞书/SSG/RSS]
                                                     │
                                              [Feedback API] ← 飞书按钮/网页
```

### 7.2 技术选型（Agent 友好）

| 层 | 选型 | 理由 |
|---|---|---|
| 语言 | Python 3.12 | agent 生态成熟、抓取/LLM 库最全 |
| 存储 | SQLite（v1）→ Postgres（v2） | 零运维起步；SQLAlchemy 抽象便于迁移 |
| 调度 | APScheduler（内嵌）→ 独立 worker | v1 单进程足够 |
| Web | FastAPI + Jinja2（存档站 SSG 用静态导出） | 简单、一个进程搞定 API+页面 |
| LLM | 分层：cheap model 分类/富化 + strong model 生成文案 | 成本控制 |
| 浏览器 | Playwright（Phase 2 截图/探针） | 仅按需 |
| 部署 | 单台 VPS + Docker Compose + Caddy | 月成本 < $15 |
| 配置 | YAML（sources/taxonomy/rubric/prompts）| 热加载，不发版 |

### 7.3 成本估算（月度）

| 项 | 估算 |
|---|---|
| LLM（300 候选/天 × 2 层调用 + 生成） | $15–30 |
| VPS（2C2G） | $6–10 |
| 域名 + 对象存储（截图） | $3 |
| **合计** | **< $45/月** |

### 7.4 仓库结构（Agent 并行开发）

```
vibe-radar/
├── PRD.md                      # 本文档
├── config/
│   ├── sources.yaml            # 信源配置
│   ├── taxonomy.yaml           # 分类体系
│   ├── rubric.yaml             # 品味 rubric（带版本）
│   └── prompts/                # 附录 D 的 prompt 模板
├── src/viberadar/
│   ├── models.py               # 所有 Pydantic 契约（先写这个！）
│   ├── store/                  # DB 层
│   ├── ingest/                 # M1：每源一个 adapter（hn.py, github.py, ph.py, ...）
│   ├── pipeline/               # M2/M3/M4：normalize.py, dedup.py, classify.py, enrich.py
│   ├── rank/                   # M5：score.py, taste.py
│   ├── compose/                # M6：digest.py, tryguide.py
│   ├── deliver/                # M6：feishu.py, rss.py, ssg.py
│   ├── feedback/               # M7：events.py, report.py
│   ├── ops/                    # M9：cli.py, console (FastAPI+htmx)
│   └── jobs.py                 # Pipeline 编排
├── web/                        # 存档站模板
├── evals/
│   ├── golden/                 # 附录 A 金样例（JSON + 期望输出）
│   ├── test_recall.py          # 召回评测
│   └── test_rank.py            # 排序评测（Iimos vs Nagare 反例等）
├── tests/                      # 单元测试
└── ops/                        # Dockerfile, compose.yaml, crontab
```

**Agent 开发原则**：
1. **契约先行**：先冻结 `models.py`（RawItem/Product/Classification/…），再并行开发模块
2. **模块隔离**：每个 adapter/模块可独立运行（CLI 直接喂样本数据），不依赖全管线
3. **评测驱动**：每个模块完成时必须在 `evals/` 跑通对应金样例
4. **配置驱动**：新增源/分类/规则 = 改 YAML，不改代码

---

## 8. 里程碑与验收（Roadmap）

### Phase 0 · 手动验证（已完成 ✅）
- 人工执行两天日报（10/9、10/10），产出金样例
- 产出：本 PRD 附录 A、信源手册附录 B

### Phase 1 · MVP 自动化（目标：2 周内 agent 完成）
**范围**：M1（HN/GitHub/PH 三源）+ M2 + M3 + M4（LLM 富化）+ M5（v1 公式）+ M5.5（极简 CLI review）+ M6（飞书 + 简单静态页）+ M7（基础事件）
**DoD**：
- [ ] 连续 7 天 09:00 准点推送，无人工干预（autopilot）
- [ ] 金样例回归：召回 ≥ 90%、Top10 重合 ≥ 60%
- [ ] 单日管线耗时 < 30 分钟，成本 < $2/天
- [ ] 任一源挂掉不影响当日发布

### Phase 2 · 品味与扩展（目标：+3 周）
**范围**：Bluesky/Reddit/RSSHub 中文源接入 + Playwright 截图与探针 + Web 存档站上线（SEO/OG）+ 邮件渠道 + taste v2（首批反馈数据训练）+ 周报
**DoD**：
- [ ] 中文源每日贡献 ≥ 5 条有效候选
- [ ] 试玩指南附带自动截图
- [ ] 存档站上线并被搜索引擎收录（≥ 50 页）

### Phase 3 · 飞轮（目标：持续迭代）
**范围**：个性化推荐（v3）+ 趋势回响模块（跨天实体追踪、新品类探测——「麦当劳 MCP 现象」可自动发现）+ 用户提交入口 + 开放 API/RSS 全文 + 社区版块
**DoD**：
- [ ] 趋势模块能自动产出「本周新品类：agent 卫生」类洞察
- [ ] 个性化开关对留存有可测量的正效应（A/B）

---

## 9. 指标（Metrics）

| 类型 | 指标 | v1 目标 | v2 目标 |
|---|---|---|---|
| 北极星 | 每周「玩过了」标记数 | 50 | 500 |
| 参与 | 日报打开率 | 40% | 50% |
| 参与 | 外链点击率（/打开） | 15% | 25% |
| 参与 | 试玩标记率（/打开） | 5% | 10% |
| 留存 | 周留存（连续订阅） | 40% | 50% |
| 供应链 | 日更准点率 | 95% | 99% |
| 质量 | 召回率（vs 金样例） | 90% | 95% |
| 质量 | 误报率（不相关进入日报） | < 10% | < 5% |
| 质量 | 去重准确率 | 95% | 99% |
| 成本 | 单日管线成本 | < $2 | < $2 |

---

## 10. 风险与对策

| 风险 | 概率 | 影响 | 对策 |
|---|---|---|---|
| API 变更/封禁（PH/GitHub） | 中 | 高 | adapter 隔离；备用抓取（页面解析）；信源冗余 |
| ToS/合规问题 | 中 | 高 | 只用官方 API + 公开页面；存元数据不存全文；标注来源 |
| LLM 成本失控 | 低 | 中 | 分层模型 + 批处理 + 缓存（同一条目不重复付费） |
| 品味漂移（读者觉得难看） | 中 | 高 | 每周品味报告；「无聊」按钮的强负反馈；rubric 版本化 |
| 刷票/自推广污染 | 高 | 中 | spam_score + 域名黑名单 + 跨源验证加成 |
| 中文源不稳定 | 高 | 中 | RSSHub 自建；人工注入兜底 |
| 日更断更（主理人忙碌） | 中 | 高 | autopilot 模式为默认；review 只是增强 |
| 单点 VPS 故障 | 低 | 中 | SQLite 每日备份到对象存储；蓝图化可 10 分钟重建 |

---

## 11. 开放问题（需评审时决断）

1. **品牌名**：Vibe Radar / 振感雷达 / 其他？
2. **首发渠道**：飞书（团队场景）vs 邮件 vs 微信生态？目标用户在哪？
3. **语言**：中文为主还是中英双语？（附录 A 为中文版式）
4. **是否公开榜单**：公开（SEO/传播）vs 私密（防刷、稀缺感）？
5. **用户体系**：v1 匿名追踪即可，是否在 Phase 2 引入账号（收藏同步、个性化）？
6. **是否接受投稿**：用户提交入口的开放时机与防滥用策略？
7. **商业化**：订阅制 / 赞助位 / 只是自用工具？（影响 roadmap 优先级）

---

## 附录 A · 金样例（质量基准）

> 用途：`evals/golden/` 的数据源；M3 召回/误报评测、M5 排序评测（含反例对）、M4 文案风格基准。
> 说明：以下为 2026-10-09 / 10-10 人工完成的两期日报（已发布版本）。

### A.1 · 10月9日刊（精选）

**AI 编码 / Agent 方向**
- **Busabase**（PH #1，354 票）— agents 的公共系统记录：人类和 Claude Code/Codex 共用的数据库+工作空间。busabase.com
- **Big arrow on the screen**（HN 406 分）— 让 AI agents 在屏幕上画箭头、框选、标注。github.com/franzenzenhofer/big-arrow-on-the-screen
- **noskip**（GitHub）— 反作弊：抓 Claude Code/Codex/Cursor 偷偷跳过、弱化测试的行为。github.com/DariusHQ/noskip
- **Together Link**（PH #4）— 把 coding agent harness 接到开源模型。together.ai/link
- **RemainderBot**（HN）— 把没用完的配额花在「生成可 review 的 PR」上。github.com/arimwilson/openremainderbot
- **Clanking**（HN）— 把 coding agents 的活动变成环境音景。github.com/chrchr/clanking
- **Mudroom**（HN）— VM 里跑 coding agents，diff 通过才落地。github.com/Kernel-Hunter/mudroom

**趣味 / 创意**
- **Taipei Rush**（HN）— GTA 式 vibe coding 游戏，台湾爆火。taipei-rush.app
- **vibepulse**（GitHub）— ESP32 硬件显示 Claude Code 配额，卡住时弹「NEEDS YOU」。github.com/ftornberg/vibepulse
- **CAD Studio**（HN）— 描述零件直接得到参数化 CAD。
- **Codyssey**（HN）— 把 Claude Code 变成像素骑士游戏。
- **mcd-order-buddy**（GitHub）— 麦当劳中国 MCP 一句话下单。

**信号**
- 「Jev」梗刷屏（HN 6+ 决策模型帖互相 callout）
- 麦当劳 MCP 连续两天出现在 GitHub 中文项目

### A.2 · 10月10日刊（精选）

**AI 编码 / Agent 方向**
- **Hypervibe**（PH #17，71 票）— 把 AI coding agents 跑在无限画布上。producthunt.com/products/hypervibe-2
- **Iimos**（HN 2 分）— 自改写的 AI 应用：整个产品是一个会重写自己的聊天框。github.com/yanglongyun/iimos
- **Chrome Relay**（HN 6 分）— 让 coding agents 用你真实 Chrome 的后台标签页。github.com/aindeev/agent-chrome-relay
- **dudes**（GitHub，Rust，5★）— 多 agent 编排 + 守护进程自验证 + 浏览器 3D 办公室。github.com/ArcfluxResearch/dudes
- **RoadRaven**（HN）— coding agents 自己维护的项目计划。github.com/Shuffzord/RoadRaven

**趣味 / 创意**
- **Should I Hire a Dev or Can I Vibecode This?** — vibecodeornot.pixlw.com
- **mcd-triple-target** — 麦当劳 MCP 配餐优化。github.com/yftu/mcd-triple-target
- **Restyle-App** — "vibe coded with claude code"，Windows 美化。github.com/lildunkie/Restyle-App
- **Celery Man** — GPT-6 复刻经典短片互动电脑。celeryman.fun

### A.3 · 金样例中的排序反例（用于 M5 回归测试）

| 反例对 | 输入 | 期望 |
|---|---|---|
| 低分有趣 vs 高分平庸 | Iimos（HN 2 分） vs Nagare（HN 15 分） | Iimos 排名更高 |
| 大厂发布 vs 草根创意 | Gemini Agent（PH 150 票） vs CAD Studio（HN 2 分） | 同刊时 CAD Studio 不应被压制到末位 |
| 跨源合并 | 麦当劳 MCP 两个仓库同日出现 | 合并为「麦当劳 MCP 生态」1 条信号，而非 2 条重复 |

---

## 附录 B · 信源手册（API 细节）

| 源 | Endpoint | 鉴权 | 关键参数 | 限额注意 |
|---|---|---|---|---|
| HN | `https://hn.algolia.com/api/v1/search_by_date` | 无 | `tags=show_hn`、`query=...`、`numericFilters=created_at_i>X,<Y`、`hitsPerPage=100` | 宽松；注意分页 `page` |
| GitHub | `https://api.github.com/search/repositories` | token | `q=created:YYYY-MM-DD topic:ai`、`sort=stars` | 认证后 30 req/min（search） |
| Product Hunt | `https://api.producthunt.com/v2/api/graphql` | developer token | `posts(order: RANKING, postedAfter: ...)` | 需申请；可降级为榜单页解析 |
| Bluesky | `https://public.api.bsky.app/xrpc/app.bsky.feed.searchPosts` | 无 | `q=...`、`since`、`until` | 宽松 |
| Reddit | `https://oauth.reddit.com/r/{sub}/new` | OAuth script app | `limit=100`、`after` | 100 QPM |
| Hugging Face | `https://huggingface.co/api/{models,spaces}?sort=trendingScore` | 无/token | — | 宽松 |
| RSSHub | 自建 `http://your-host:1200/{jike,v2ex,sspai}/...` | 无 | — | 自建无限制 |

**合规红线**：只存标题/URL/元数据/摘要（≤ 200 字）；图片只存 URL 不转存（截图除外，标注来源）；遵守各站 robots；对需登录内容不做采集。

---

## 附录 C · 术语表

| 术语 | 定义 |
|---|---|
| 候选 Candidate | 通过 M2/M3、尚未发布的条目 |
| 富化 Enrich | 补齐一条候选的展示与试玩信息 |
| 期号 Edition | 一天的日报（含发布状态） |
| Autopilot | 无人工干预的全自动发布模式 |
| 金样例 Golden | 人工完成的历史日报，作为评测基准 |
| 回响 Echo | 同一实体/品类跨天重复出现的趋势信号 |

---

## 附录 D · Prompt 模板（存于 `config/prompts/`）

### D-1 · 分类判定（cheap model，批量）

```
你是 vibe coding 生态的新品分类器。对每条候选输出 JSON：
{"relevant": bool, "category": "taxonomy key", "confidence": 0-1,
 "spam_score": 0-1, "reason": "一句话"}

收录标准：{PRD 2.5 全文}
分类体系：{taxonomy.yaml 全文}
spam 信号：与产品无关的融资/新闻、自我推广特征、描述空洞。
候选（20-50 条 JSON 数组）：{{items}}
```

### D-2 · 趣味度打分（cheap model）

```
依据以下 rubric 给这条产品打 0-10 的「有趣度」，并给出各维度分：
创意度/可玩性/完成度/反直觉/生态位（各 0-5）。
重要：社区分数与有趣度无关，忽略任何票数/star 信息，只判断内容本身。
产品信息：{{product}}
输出：{"interest": 0-10, "dims": {...}, "why": "一句话"}
```

### D-3 · 试玩指南生成（strong model）

```
为以下产品写「30 秒试玩指南」，中文，40-80 字，结构：
① 打开：{url}（免费/需登录/需自托管）
② 第一步：{一个具体动作}
③ 看什么：{wow 点}
④ 坑：{注意事项，无则省略}
铁律：只允许基于以下材料的事实，禁止编造功能；不确定就省略。
产品材料：{{enriched_content}}
```

### D-4 · Autopilot 发布决策（strong model）

```
你是 Vibe Radar 的主理人。基于今天的 {{N}} 条候选（含信号与打分），
产出今日日报：选 8-15 条，按板块分组（AI 编码/趣味创意/信号），
每条给：名称、一句卖点、30 秒试玩指南（引用既有字段，可润色不可编造）、
社区数据标注（来源+票数/star）。
排序原则：创意度 > 热度；低分高创意必须保护；同品类不超过 40%。
注意：宁缺毋滥，选不出来就少发；今日无好货则回答「今日建议停刊」。
候选：{{candidates_with_signals}}
```

---

*文档结束 · 下一步：冻结 `models.py` 契约 → 建 repo → 按 Phase 1 并行开发 →
每模块以附录 A 金样例回归验收。*
