# 评测目录（PRD 11.1）

`viberadar eval --dataset-version <版本名>` 只会校验 `evals/<版本名>/` 下的完整性；
缺少以下任一文件会拒绝运行并提示“评测资料不完整”，不生成虚假召回率（E25）。

| 文件 | 内容 |
|---|---|
| `manifest.json` | 样本来源、数量、许可、hash、缺失字段、版本及训练/验证/留出切分 |
| `raw_items.jsonl` | 源条目、来源、时间窗口、抓取时间和当时热度 |
| `labels.jsonl` | 相关性、分类、spam 证据、门槛、指南 claims、选择偏好、标注者与日期 |
| `entities.jsonl` | 同产品正例、不同产品负例、主题关系、发布事件关系 |
| `expected_editions.json` | 参考人工选择与排序，允许多个合理顺序 |

使用 `--predictions predictions.jsonl`（每行 `{"item_id": ..., "relevant": true|false}`）
可计算留出集的相关性 recall / precision，并与门槛比较：

- 分类召回 TP/(TP+FN) ≥ 90%
- 分类 precision TP/(TP+FP) ≥ 85%

注意：

- 两天样例只用于规则开发；新增不同日期样本作留出验收；同一产品不得跨切分造成泄漏。
- 只提交精选名单而没有全量输入与负例时，**不会**计算指标。
- 建议首版 ≥200 条，含 ≥50 条相关候选、≥50 条负例、≥30 组实体正/负对，覆盖 ≥7 个日期。
- 关键分歧由第二名审阅者复核；单人标注需间隔 ≥48 小时盲复核并注明局限。
