# 比较两个算例或结果

## 目录

- 比较什么
- 准备两个 result source
- 对齐口径
- 逐项比较
- 汇总与交付

本文说明如何快速比较两个算例（或同一算例的两个方案）的计算结果。所有比较都建立在服务端结构化接口之上，不下载 `.tr`；每个结果分别走“动态发现 -> 精确 group/曲线 -> 有界本地聚合”，再在 Agent 侧比对。

## 比较什么

- 只比较已完成且有 `result_path` 的任务/结果；未完成的先等待或报告状态。
- 输入侧对比（设备表、参数、场景）解决“两个算例差在哪”；结果侧对比解决“两个方案好在哪”。
- 常见比较维度：新能源消纳（`_result.key_summaries` 或工作位置聚合）、月度风险代表时刻（`neps_power` 默认表）、系统/分区负荷与备用、指定设备曲线、成本与惩罚（`_result.cost_and_penalty`）、市场电价（job type 301）。具体 group 以每个结果的动态 inventory 为准。

## 准备两个 result source

先各自定位并取得 `result_path`（或 task ID）：

```bash
teap task status --finished-only --finished-page -1 --finished-page-size 1
teap result search --filter <name> --page-size 20
```

两个结果是不同方案时，常见来源：

- 两个独立任务：`result groups <id-a>` 与 `result groups <id-b>`；
- 同一任务不同场景：增加 `--scenario` 读取 `_result.scenario_balance_df...` 或场景设备曲线；
- 从历史 task 分叉出的多个修改方案：见 [historical-branching.md](historical-branching.md)。

## 对齐口径

比较前必须确认两侧口径一致，否则数值差异是口径差异而非方案差异：

- **场景**：确认两侧 `case_info.scenario_selected` 与曲线 `scenario` 指向同一场景。
- **job type / 计算模式**：`3/4/5/6/101/102/103/105/301` 之间结构不同，一般不可直接比对；如确需比对，逐 group 确认两侧 inventory 都存在该 group。
- **单位**：两侧查询使用同一个精确 `--unit <exact-unit>`，确认 `unit.applied=true`；不要一边 MW 一边万千瓦直接比数。
- **时间轴**：确认两侧仿真时长/步长一致（如 `neps_alltime_power.<hour>` 的合法索引范围一致）。
- **设备集**：两侧 device/inventory 的 element、curve、设备索引集一致；不要拿一边的 `wind Pwind --device-index 7` 去比另一边的完全不同的设备。

## 逐项比较

对每个比较维度，两侧分别执行同一命令，再本地比对返回的有界数据：

```bash
# 关键摘要（含新能源消纳率）
teap result get <id-a> -g _result.key_summaries
teap result get <id-b> -g _result.key_summaries

# 月度风险代表时刻（neps_power 默认表）
teap result get <id-a> -g _result.balance_df.neps_power -l --unit 兆瓦
teap result get <id-b> -g _result.balance_df.neps_power -l --unit 兆瓦

# 系统/分区工作位置曲线
teap result work-position <id-a> --unit 兆瓦
teap result work-position <id-b> --unit 兆瓦

# 指定设备曲线（服务端过滤到同一设备索引）
teap result curve <id-a> wind Pwind --device-index 7 --unit 万千瓦
teap result curve <id-b> wind Pwind --device-index 7 --unit 万千瓦

# 成本与惩罚
teap result get <id-a> -g _result.cost_and_penalty
teap result get <id-b> -g _result.cost_and_penalty
```

本地比对建议：

- **逐时刻比率/差值**：先确认两侧时间索引一一对应，再逐时刻相减或求差；对同一曲线的 `min/max/mean`（可用 `--summary-only`）做摘要对比；
- **消纳率**：两侧分别按 `消纳/(消纳+弃电)` 计算后对比，分母为零时刻标为不可计算；
- **风险登记**：分别读取月度 `neps_power` 默认表定位最严缺口/最小时刻，比较其时刻与量级；
- **设备差异**：只比较两侧都存在的设备；某侧独有设备单独标注，不并入统一指标。

口径不一致或两侧 inventory 结构不同时，停止自动对齐并如实说明差异来源；不要编造换算或跳过缺失一方。

## 汇总与交付

按以下形式汇报比较结果：

- 两个 source（task ID + result path + job type + 场景）；
- 每个比较维度的量化差异（绝对值与相对值，带单位）；
- 差异结论（如“B 方案月度最大缺口比 A 减少 120 MW”“消纳率从 93.1% 升至 95.4%”）；
- 数据来源 group 与所用命令；
- 无法对齐或缺失的维度及原因。

需要把某侧结果落到本地交付文件时，按用户要求使用 [teapresult/result-lifecycle.md](../teapresult/result-lifecycle.md) 的导出命令；比较过程本身不下载任何文件。
