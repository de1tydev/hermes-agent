# AI 使用量日报模板

本模板只适用于 `collect_ai_usage_insights.py` 输出的 `ai_usage_insights.v1` JSON。日报事实必须来自该 JSON；不要基于接口常识、历史经验或自然语言推测补数。

---

**AI 使用量日报（<report_context.window.start_at> 至 <report_context.window.end_at>）**

统计周期：`<report_context.window.start_at>` 至 `<report_context.window.end_at>`（Asia/Shanghai，左闭右开）
基线：最近 `<report_context.baseline.workdays_requested>` 个标准工作日同等窗口，成功 `<report_context.baseline.workdays_successful>` 个
数据口径：`<current.coverage_scope>` / `<current.covered_totals.confidence>`
费用单位：USD；所有费用数值加 `$` 前缀，保留 2 位小数。

若 `data_quality.coverage_warning=true`，在标题下方加一句：

> 注：本报告仅代表接口返回数据覆盖范围内的趋势，不可表述为全公司完整总量。

注意：全局免责声明不能替代正文逐句限定。若 `data_quality.coverage_scope`、`current.coverage_scope`、`current.covered_totals.scope` 或任一指标 `scope` 为 `ranked_rows_only` / `limit_boundary_unknown`，正文中每一句涉及总量、占比、份额、合计、趋势、较基线变化、整体费用、整体 Token 或集中度，都必须在同一句内写“返回数据覆盖范围内”或“返回范围”。

弱覆盖范围下，正文禁止未限定使用：“当前窗口总 Token”“当前窗口总费用”“占总费用”“整体费用”“总费用”“总 Token”“合计约占”。需要表达这些概念时改写为“返回数据覆盖范围内总 Token”“返回数据覆盖范围内费用（USD）”“返回范围内占费用约”“返回数据覆盖范围内费用上升”“返回范围内 Top 用户合计约占”。表格列名“总 Token”可以保留。

若 `data_quality.identity_warning=true`，在用户趋势段落加一句：

> 注：用户跨日趋势受身份稳定性限制，仅作为候选观察。

---

## 一、用户用量排行与用量大户

用 `current.users[]` 输出 Top 用户表，最多 20 行。

| 排名 | 用户 | 请求数 | 总 Token | Input | Output | Cache Read | Cache Creation | 费用 |
|------|------|--------|----------|-------|--------|------------|----------------|------|
| `<rank>` | `<display_name>` | `<requests>` | `<total_tokens>` | `<input_tokens>` | `<output_tokens>` | `<cache_read_tokens>` | `<cache_creation_tokens>` | `<cost>` |

字段映射：

- 用户：`display_name`
- 排名：`rank`
- 请求数：`requests`
- 总 Token：`total_tokens`
- Input：`input_tokens`
- Output：`output_tokens`
- Cache Read：`cache_read_tokens`
- Cache Creation：`cache_creation_tokens`
- 费用：`cost`，按美元展示，格式 `$<cost>`，保留 2 位小数

表格后写 2～4 条大户分析，优先使用：

- `user_insights.heavy_users[]`
- `user_insights.concentration`
- `baseline_comparison.users[]`

写法要求：

- 当 `scope=ranked_rows_only` 或 `limit_boundary_unknown` 时，写“返回数据覆盖范围内排名靠前/占比较高”，不要写“全公司第一/全公司占比”。
- 当 `scope=ranked_rows_only` 或 `limit_boundary_unknown` 时，每一句用户合计、用户占比、用户趋势或较基线变化都必须在同一句内写“返回数据覆盖范围内”或“返回范围”；不要只靠开头免责声明。
- `concentration.label=suppressed_when_limit_boundary_unknown` 时，不写集中度标签，只可写覆盖范围内 Top 用户贡献。
- 不输出 `entity_key`，不输出 raw key 名、key ID 或 source field。

## 二、公司趋势与费用趋势

使用 `company_trend` 和 `baseline_comparison.company` 写 3～6 条。

必须覆盖：

- Token 趋势：`company_trend.token_trend.latest_vs_avg_percent`、`last_3_vs_first_3_percent`、`slope_percent_per_workday`、`volatility_cv`
- 趋势分类：`company_trend.classification.primary` 与 `labels`
- 费用趋势：`company_trend.cost_trend.latest_vs_avg_percent`、`latest_vs_first_percent`
- 请求趋势：`company_trend.request_trend`
- 单位成本：`cost_efficiency.cost_per_million_tokens`、`cost_per_request`
- Token/费用背离：`cost_efficiency.token_cost_divergence`

解释规则：

- 当 `data_quality.coverage_scope=ranked_rows_only` 或 `limit_boundary_unknown` 时，公司趋势和费用趋势每一句都必须就地限定为“返回数据覆盖范围内”或“返回范围”，例如“返回数据覆盖范围内总 Token ... 较基线均值上升 ...”“返回数据覆盖范围内费用（USD）...”；不要写未限定的“当前窗口总 Token”“当前窗口总费用”“整体费用上升”“占总费用”。
- Token 与费用同步上升：可写“真实用量增长迹象”。
- Token 基本持平但费用上升：可写“模型结构或单位成本可能变贵”。
- Token 上升但费用涨幅较小：可写“缓存命中或低价模型占比可能改善单位成本”。
- 费用、成本字段为 `unavailable`、`null` 或被 suppress 时，写“费用趋势不可用”，不要推断。
- `classification.primary=spike|volatile|up|down|flat|unknown` 必须按脚本输出写，不要重分类。

费用单位展示要求：

- AIStat 费用数值均按美元展示。
- 费用列写“费用（USD）”，数值格式为 `$<amount>`，保留 2 位小数。
- 单位成本写 `$<amount>/M Token`、`$<amount>/请求`。
- 不使用“人民币”、“RMB”、“CNY”或“¥”。

## 三、波动用户与变化用户

优先使用以下字段：

- `user_insights.volatile_users[]`
- `user_insights.rising_users[]`
- `user_insights.falling_users[]`
- `user_insights.newly_active_users[]`
- `user_insights.advisory_user_trends[]`

推荐写 3～5 条：

1. 用户 `<display_name>` 当前 `<current>`，基线 `<baseline>`，变化 `<percent_change>%`，原因 `<reason>`。
2. 若 `confidence=low`，加“低置信度候选”。
3. 若 `history_evidence.zero_fill_applied=false`，不要把缺席窗口写成 0。
4. 若只能进入 `advisory_user_trends`，写“候选波动/候选上升”，不要写强结论。

无可用用户趋势时，写“当前 JSON 未提供足够稳定身份或历史窗口来形成强用户趋势结论”。

## 四、模型结构与缓存驱动

用 `current.models[]` 输出 Top 模型表，最多 20 行。

| 排名 | 模型 | 请求数 | 总 Token | Input | Output | Cache Read | Cache Creation | 费用 |
|------|------|--------|----------|-------|--------|------------|----------------|------|
| `<rank>` | `<display_name>` | `<requests>` | `<total_tokens>` | `<input_tokens>` | `<output_tokens>` | `<cache_read_tokens>` | `<cache_creation_tokens>` | `<cost>` |

表格后写 3～6 条，必须覆盖：

- 模型结构：`model_insights.top_models[]`、`share_changes[]`、`new_or_missing_models[]`
- 昂贵模型：`model_insights.expensive_models[]`；若 `cost_metrics_suppressed=true`，明确写“模型费用指标被抑制”
- 请求强度：`model_insights.request_intensity_changes[]`
- 缓存驱动：`model_insights.cache_heavy_models[]`
- Token 组成：`token_structure.cache_read_ratio`、`input_ratio`、`output_ratio`、`cache_creation_ratio`、`fresh_token_ratio`、`dominant_driver`

缓存解释规则：

- 当 `data_quality.coverage_scope=ranked_rows_only` 或 `limit_boundary_unknown` 时，Token 结构、缓存占比、模型份额和 Top 模型趋势也必须写成“返回数据覆盖范围内”或“返回范围”内的结构，不要写成全量结构。
- `dominant_driver=cache_read`：可写“本期 Token 主要由 Cache Read 驱动”。
- `cache_read_ratio` 高且费用涨幅低于 Token 涨幅：可写“缓存读取可能压低单位成本”。
- `token_component_warning=true` 时，必须写“Token 组成字段存在缺失或派生，缓存判断需谨慎”。

## 五、重点结论

优先把 `candidate_highlights[]` 中分数最高且符合 `llm_guidance` 的项目写成 3～5 条要点。

不要在报告末尾输出“数据质量与降级”字段清单；不要逐项列出 `status`、`coverage_scope`、`baseline_workdays_*`、`backend_window_alignment`、`must_mention`、`must_not_claim`。

如果 `status=partial`，只用 1 句人话说明降级原因；如果 `coverage_warning=true` 或 `identity_warning=true`，只保留必要提示句。

如果脚本失败、退出非 0、stdout 非 JSON 或 `schema_version` 不是 `ai_usage_insights.v1`：

```text
AI 使用量日报生成失败。

原因：<脚本退出码或 JSON 校验错误>
stderr：<脱敏后的 stderr 摘要>
调用参数：end_at=<...>, hours=24, baseline_workdays=10, trend_workdays=10, limit=100, top_n=20

未生成日报正文，避免使用不完整或伪造数据。
```

## 格式化规则

- Token 大数优先用 K/M/B：`>=1_000_000_000` 为 B，`>=1_000_000` 为 M，`>=1_000` 为 K。
- 百分比保留 1～2 位小数。
- 费用值按 USD 展示，统一加 `$` 前缀并保留 2 位小数；单位成本写 `$<amount>/M Token` 或 `$<amount>/请求`。
- 不使用人民币、RMB、CNY 或 ¥。
- 当 `coverage_scope=ranked_rows_only` 或 `limit_boundary_unknown` 时，正文每句 total/share/trend 都必须就地写“返回数据覆盖范围内”或“返回范围”；不能用全局免责声明代替。
- `unavailable`、`null`、缺失字段写“不可用”。
- `percent_change` 分母无基数时写“基线无基数”。
- 不输出 JSON 内部指纹字段，例如 `entity_key`。
- 不输出 raw key names、key IDs、source fields、endpoint secrets、Authorization、Cookie 或 token。
