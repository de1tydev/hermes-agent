---
name: ai-usage-report
description: 生成 AI 使用量日报/排行榜。用户要求“AI 用量日报”“AI 使用量报告”“AI 用量排行”“每日 AI 使用量榜单”“18 点用量日报”或让 Hermes 定时发送 AI 用量统计时触发。默认通过本 skill 内 Python 脚本采集 TODE AI 用量分析 daily-usage-rankings 数据并输出结构化洞察 JSON。
platforms: [linux]
prerequisites:
  commands: [python3]
---

# AI 使用量日报 Skill

## 目标

生成可直接发送给 Master 的 AI 使用量日报。默认日报统计“最近 24 小时”，使用触发时刻的 Asia/Shanghai `end_at`，并以最近 10 个标准工作日同等窗口作为基线。日报事实必须来自脚本输出的 `ai_usage_insights.v1` JSON，不要凭经验编数字或推断具体项目原因。

默认报告包含：

1. 用户用量排行与用量大户
2. 模型用量排行与模型结构变化
3. 公司趋势、费用趋势、缓存驱动与请求强度
4. 波动用户、上升/下降用户和新活跃用户候选
5. 必要的数据范围提示（不要展开 JSON 字段清单）

## 默认调用方式

使用 `exec` 调用 `python3`，不要用 `web_fetch`。默认先运行本 skill 内脚本，再把 stdout JSON 作为 `references/template.md` 的唯一事实输入写日报。

```bash
END_AT=$(TZ=Asia/Shanghai date +%Y-%m-%dT%H:%M:%S%:z)
python3 {baseDir}/scripts/collect_ai_usage_insights.py \
  --end-at "${END_AT}" \
  --hours 24 \
  --baseline-workdays 10 \
  --trend-workdays 10 \
  --limit 100 \
  --top-n 20
```

默认 endpoint 为完整接口地址：

```text
https://aistat.tode.ltd/analytics/api/daily-usage-rankings
```

endpoint 优先级为 `--endpoint-url` > `AI_USAGE_ENDPOINT_URL` > 默认 URL。若未来需要鉴权，只能通过环境变量读取 token，例如 `AI_USAGE_API_TOKEN`；不要在命令行、日报、错误消息或调试输出里写入 token、Authorization、Cookie、Key ID、Key 名或 source field。

## 执行规则

- 使用 Hermes terminal 调用 `python3` 或只读 `curl` 检查；禁止用网页抓取工具替代接口调用。
- 默认日报必须使用脚本 JSON，不再直接 `curl` 接口后手写洞察。
- `end_at` 必须是触发时刻的 `+08:00` ISO 时间；不要改成当天 23:59 或自然日结束。
- 默认窗口为 `hours=24`，表示 `[昨日触发时刻, 今日触发时刻)`。
- 默认基线为 `baseline_workdays=10`，标准工作日只按周一至周五，不含中国法定节假日/调休。
- 默认趋势窗口为 `trend_workdays=10`。
- 默认 `limit=100`、`top_n=20`；`top_n` 不能超过 `limit`。
- 脚本串行请求接口；不要并发压接口。
- stdout 只接受脚本最终 JSON；stderr 只作为失败/进度信息，不作为事实来源。

## 脚本输出口径

脚本输出 `schema_version=ai_usage_insights.v1`，核心字段包括：

- `report_context`：标题、生成时间、窗口、基线、趋势天数。
- `current.users[]` / `current.models[]`：当前窗口用户与模型排行。
- `current.covered_totals`：返回数据覆盖范围内的聚合值。
- `baseline_comparison`：当前窗口相对最近工作日均值的对比。
- `company_trend`：公司 Token、费用、请求趋势和趋势分类。
- `user_insights`：用量大户、波动用户、上升/下降用户、新活跃用户、集中度。
- `model_insights`：Top 模型、份额变化、昂贵模型、缓存重模型、请求强度变化。
- `token_structure`：Input/Output/Cache Read/Cache Creation 占比与主驱动。
- `cost_efficiency`：单位 Token 成本、单位请求成本、Token/费用背离。
- `candidate_highlights`：可优先写入日报的候选洞察。
- `data_quality` 与 `llm_guidance`：覆盖范围、缺失窗口、身份置信度、必须披露和禁止声称的内容。

所有事实判断必须检查 `scope` 与 `confidence`。当 `scope` 是 `ranked_rows_only` 或 `limit_boundary_unknown` 时，只能写“返回数据覆盖范围内”或“返回范围”，不得写“全公司总量”。必要的数据质量风险只在正文中用 1～2 句人话提示，不要在报告末尾展开 `status`、`coverage_scope`、`must_mention`、`must_not_claim` 等 JSON 字段清单。

## 弱覆盖范围逐句表述规则

当 `data_quality.coverage_scope`、`current.coverage_scope`、`current.covered_totals.scope` 或任一用于造句的指标 `scope` 为 `ranked_rows_only` 或 `limit_boundary_unknown` 时，不能只依赖标题下的全局免责声明。每一句涉及总量、占比、份额、合计、趋势、较基线变化、整体费用、整体 Token 或集中度的句子，都必须在同一句里明确使用“返回数据覆盖范围内”或“返回范围”。

禁止在弱覆盖范围下输出未就地限定的短语，包括但不限于：

- “当前窗口总 Token”
- “当前窗口总费用”
- “占总费用”
- “整体费用”
- “总费用”
- “总 Token”
- “合计约占”

如果必须使用这些概念，必须改写为同一句内带范围限定的表达，例如“返回数据覆盖范围内总 Token”、“返回数据覆盖范围内费用（USD）”、“返回范围内占费用约”、“返回数据覆盖范围内费用上升”、“返回范围内 Top 用户合计约占”。表格列名“总 Token”可以保留；正文句子和要点不能出现未限定的弱 scope 总量/份额/趋势表述。

## 费用单位规则

AIStat 接口中的费用数值均按美元处理；日报里所有费用与单位成本都使用 `$` 前缀并保留 2 位小数。

- 费用列名写“费用（USD）”。
- 示例：`$515.81`、`$0.56/M Token`、`$0.06/请求`。
- 不使用“人民币”、“RMB”、“CNY”或“¥”。
- 费用、成本字段为 `unavailable`、`null` 或被 suppress 时，写“费用趋势不可用”，不要推断。

## 失败降级

- 如果脚本退出非 0，不要生成日报，也不要伪造 JSON；直接报告脚本失败、退出码、stderr 摘要和已脱敏调用参数。
- 如果 stdout 不是合法 JSON，视为失败；停止生成日报。
- 如果 JSON `status` 是 `partial`，可以生成日报，但必须披露 `data_quality.missing_periods`、`deadline_skipped`、`api_errors`、`coverage_warning` 等降级原因。
- 如果 `users` 或 `models` 为空，相关表格写“暂无数据”，不要补造排行。
- 如果字段为 `unavailable` 或 `null`，写“不可用/无基数”，不要当作 0。
- 如果 `cost` 缺失或 `model_insights.cost_metrics_suppressed=true`，不要写模型费用强结论。
- 不得泄露 raw `key_names`、`key_ids`、`source_fields`、endpoint secret、token、Authorization header 或 Cookie。

## 报告输出

严格参考 `references/template.md`。优先使用脚本提供的紧凑展示和候选洞察；缺少展示字段时再自行把数值格式化为 K/M/B。分析原因只能写成“可能原因，需结合实际任务确认”。

标题使用：

```text
AI 使用量日报（<report_context.window.start_at> 至 <report_context.window.end_at>）
```

发送前检查：

1. 使用的是脚本 JSON，而不是直接接口原始 JSON。
2. 已包含公司趋势、费用趋势、用量大户、波动用户、模型结构、缓存驱动，以及必要的数据范围提示。
3. 不在末尾输出“数据质量与降级”的 JSON 字段清单。
4. 未输出任何 token/secret/raw key/source field。
