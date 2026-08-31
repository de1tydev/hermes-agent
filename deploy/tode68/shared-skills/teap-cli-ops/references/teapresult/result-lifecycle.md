# 结果读取、导出与删除

## 目录

- 对象边界
- 动态发现结果内容
- 读取精确 group
- 常用中长期 group
- 专用结果命令
- 按用户要求导出结果文件
- 删除结果

负责对某个已完成 task/`.tr` 的结构化读取、按用户要求导出与删除。常见分析手法见 [teapresult/query-recipes.md](query-recipes.md)；交易结果见 [teapresult/market-results.md](market-results.md)。

## 对象边界

`result/.tr` 是计算完成后的输出。只有完成且包含 `result_path` 的记录才是结果；“计算完成的算例”“最新完成”“结果、报告”从 task/result 路由，不查 `case list`。

## 动态发现结果内容

先列 CLI 已知的常用 group；有具体结果时必须动态发现该文件实际包含的数据：

```bash
teap result groups
teap result groups 12345
```

动态返回包括实际 `_result.*` key、普通/场景设备曲线 inventory、曲线的 `curve_units`、分区及精确 bus-index。`curve_units` 中的 `name/coefficient/type` 直接来自当前 Core 响应；不要从静态常用列表推断某个结果一定包含某条曲线或单位。

不要猜 `renewable`、`power_balance`、`consumption` 等自然语言 group。某个猜测 group 返回无法解析不代表 `.tr` 损坏；先运行动态 `result groups <source>`。`result get --to-list` 仍要求有效 group，不是列 group 的方法。

## 读取精确 group

```bash
teap result get 12345 -g parameter
```

对于带单位的 JSON 结果，第一次不加 `--unit` 查询并读取 `available_units`；其中每个单位的值都等于 Core 原始 `data × coefficient`。选择精确名称后让 CLI 执行换算：

```bash
teap result get 12345 -g _result.balance_df.neps_power -l --unit 兆瓦
teap result get 12345 -g _result.balance_df.neps_electricity_monthly -l --unit 兆瓦时
```

普通 `json` 会返回 `unit.name/coefficient/applied=true`；未指定单位时返回 `unit.applied=false` 与 `available_units`，数据保持 Core 原值。`raw-json` 始终保持服务端原始响应，因此不能与 `--unit` 合用。`--unit` 也不与 `--to-csv-format` 合用，因为 CSV/XLSX 的换算和单位标题由 Core 控制。单位不存在或响应没有单位元数据时，`result_unit_unavailable` 为不可原样重试错误；按 hint 去掉 `--unit` 检查实际元数据，不得猜系数。

语法护栏：任务 ID 或服务端 `.tr` 路径是位置参数，`-g/--group` 必填；没有公开的 `-t` 或 `--task-record-id`。数字任务 ID 会先解析为 `result_path`，再读取结果，这也适用于 job type 301。需要后端过滤参数时使用 `--extra-param-json/--extra-param-file`。

## 常用中长期 group

- `parameter`
- `_result.key_summaries`
- `_result.cost_and_penalty`
- `_result.balance_df.neps_power -l`
- `_result.balance_df.neps_electricity_monthly -l`
- `_result.balance_df.neps_alltime_power.<hour> -l`
- `_result.balance_df`
- `_result.balance_df.zone_power.<zone>`
- `_result.scenario_balance_df`
- `_result.scenario_balance_df.zone_power.<zone>`
- `_result.result_dict.<element>.<curve>[.<bus-index#...>]`

新能源消纳优先读 `_result.key_summaries`，字段：

- `data.renewable_util_rate.wind`
- `data.renewable_util_rate.solar`
- `data.renewable_util_rate.renewable`

这些通常是 0-1 小数，面向用户转为百分比。

月度默认 `_result.balance_df.neps_power` 每月从完整时序中选取风险代表行：主排序为四位小数舍入后的 `power_balance` 升序；全系统主值相同时，再按缺口合计降序、负荷及运行特征排序。因此通常是当月最严重缺口，若没有缺口则是盈余/裕度最小时刻。它不是唯一可查询时刻。完整快速查询链路见 [teapresult/query-recipes.md](query-recipes.md)。

需要 CSV 时按 `result get -h` 使用 `--to-csv-format` 和该命令的文件输出选项。

市场模拟结果不要套用中长期摘要路径。先运行：

```bash
teap result market groups 12345
```

再按实际存在的电价、机组经济性和统计维度查询。完整语义和命令见 [teapresult/market-results.md](market-results.md)。

## 专用结果命令

任意零基仿真时刻电力平衡、系统/分区工作位置图和设备曲线：

```bash
teap result balance-hour 12345 750
teap result work-position 12345
teap result work-position 12345 --zone "华东"
teap result work-position 12345 --scenario
teap result work-position 12345 --scenario --zone "华东"
teap result curve 12345 wind Pwind_curt --metadata-only
teap result curve 12345 wind Pwind_curt --device-index 7
teap result curve 12345 wind Pwind_curt --device-index 7 --unit 万千瓦
```

设备索引先由 `--metadata-only` 获取；该请求返回名称和 index，不返回时序矩阵。不得猜 index，也不要为找索引先拉取全量设备曲线。

## 按用户要求导出结果文件

结构化结果接口能够读取 `.tr` 中保存的 result key 和设备时序，普通结果分析必须使用这些服务端接口。只有用户明确要求取得、导出、交付、保存或在本地检查文件时才走本节。数据未暴露或后端无法解析时，报告已尝试的 group/curve 和接口限制；未经用户明确要求，不得自行下载 `.tr` 解析。

导入已有本地 `.tr`：

```bash
teap result import-tr ./a.tr ./b.tr
```

导入结果只创建或登记结果对象，不把它当作可编辑 case。

下载 `.tr`：

```bash
teap result download-tr 12345 -o ./result.tr
```

也可传明确的 result path。不要把 case path 传给 `download-tr`。

用户明确要求把结果内嵌输入模型导出到本地时：

```bash
teap result download-tc 12345 -o ./result-case.tc
```

重新生成服务端报告不下载文件；用户明确要求本地报表或压缩包时再执行对应下载命令：

```bash
teap result regenerate-reports 12345
teap result download-summary 12345 --unit MW -o ./summary.xlsx
teap result download-detail --task-id 12345 --task-id 12346 -o ./detail.zip
teap result download-balance --result-path /server/a.tr -o ./balance.zip
```

批量 detail/balance 必须只选择 task ID 或 result path 一类来源，不能混用。result path 是 `.tr` 的服务端路径，不是 case path。

## 删除结果

只删除已完成且包含 `result_path` 的任务/结果记录：

```bash
teap result delete 12345 --confirm
```

可以传多个 task ID。该命令删除完成结果、对应任务历史和关联日志，但保留源 `.tc` case；需要删除输入模型时使用 [teapcase/case-lifecycle.md](../teapcase/case-lifecycle.md)。等待中、计算中、停止中、失败或没有 result path 的任务会返回 `result_not_deletable`，执行 hint 后停止，不要用另一个 ID 盲试。
