# 任务、结果与日志

## 目录

- 对象边界
- 启动任务
- 查询与等待
- 取消任务
- 读取结构化结果
- 按用户要求导出结果文件
- 删除结果
- 运行方式筛选
- 日志诊断
- 完成汇报

## 对象边界

`case/.tc` 是可编辑输入，`task` 是一次执行记录，`result/.tr` 是完成后的输出。

“计算完成的算例”“最新完成”“结果”“报告”必须从 task/result 路由。先查 `task status`，选择完成且有 `result_path` 的记录；不要从 `case list` 猜哪个 case 已运行。

## 启动任务

`task start` 按 source 类型执行两种明确行为：

- 一个或多个正整数 task ID：一次请求启动未开始任务或重启已修复的失败任务，复用原 ID、job type 和提交参数，不创建 task，也不下载/复制 `.tc`；计算中或等待中的 task 作为幂等项，不重复提交。
- case/result 服务端路径：按指定 `--job-type` 创建新 task；只有需要独立执行记录时才使用此形式。

原地启动已有任务：

```bash
teap -o json task start 4776
teap -o json task start 4776 4777 4778
```

ID 形式不接受 `--job-type`、`--note`、`--chronology-reduction-method` 或 job config，也不能混入路径或重复 ID。CLI 会在写入前检查整批目标：任一任务不存在、停止中、已完成或状态不允许时整批不提交；失败任务必须逐项完成 `log analyze` 和修正，不能把未改变的失败任务加入批次。成功后读取 `submitted_task_ids`、`already_active_task_ids` 和 `tasks`，确认后端没有静默忽略任何目标。

从服务端 case 路径创建新任务：

```bash
teap -o json task start "$CASE_PATH" --job-type 4
```

单个或批量 ID、路径形式均可同步等待，例如：

```bash
teap -o json task start 4776 --wait
teap -o json task start 4776 4777 --wait
```

完整 job type、算法配置名、输入类型、启动入口和模式专属前置条件见 [computation-modes.md](computation-modes.md)。选择或解释类型前必须读取该文档。特别注意：普通 `task start` 不支持启动 `104` 短路计算；不要仅因 `task job-types` 列出该类型就提交 `.tc`。

不要假设每个 case 支持全部类型。用户未要求执行时，不要擅自启动耗时任务，也不要在某个类型失败后改用名称相近的类型。

查看 CLI 当前识别的 job type 和来源：

```bash
teap -o json task job-types
```

高级任务配置：

- `--note <text>`：备注；
- `--chronology-reduction-method <value>`：时序削减方法；
- `--job-config-json/--job-config-file`：任务级配置；
- `--poll-interval`：等待轮询间隔；
- `--timeout`：任务等待超时。

任务配置不应替代 case parameter 的持久设置，除非后端明确把该字段定义为任务级覆盖。

## 查询与等待

列任务（默认同时返回未完成和已完成页）：

```bash
teap -o json task status
```

分页、过滤和最新完成结果：

```bash
teap -o json task status --finished-only --finished-page -1 --finished-page-size 1
teap -o json task status --finished-only --finished-page 3 --finished-page-size 20
teap -o json task status --unfinished-only --filter demo --job-type 4
```

`--finished-page -1` 使用后端最后一页语义。找“最新完成”时将完成页大小限制为 1，不要拉取完整历史。`--filter` 匹配算例名或备注，`--job-type` 可重复。

查单个任务：

```bash
teap -o json task status --task-record-id 12345
```

`task status 12345` 是错误语法。

等待已有任务：

```bash
teap -o json task wait 12345 --timeout 3600 --poll-interval 2
```

等待失败时先读结构化 `failure.findings` 和 `failure.next_commands`。`task_failed` 不可原样重试。

## 取消任务

“取消算例计算”指取消 `task`，不是删除 case 或 result。先检查单任务状态：

```bash
teap -o json task status --task-record-id 12345
```

用户明确要求取消后执行：

```bash
teap -o json task cancel 12345 --confirm
```

可一次取消多个等待中或计算中的任务。等待中会退出队列，计算中会进入停止中并向求解过程发送协作停止信号；市场模拟 job type 301 也使用该停止链路。返回成功不表示进程已经瞬时退出，应继续查询状态，直到离开等待中、计算中或停止中。

`task_not_cancellable`、`confirmation_required` 都是不可原样重试错误。已经停止中时只监控状态；已经完成时读取或删除结果；已经失败时分析日志。不要改用 `case delete` 或 `result delete` 强行停止活动任务。

## 读取结构化结果

先列 CLI 已知的常用 group；有具体结果时必须动态发现该文件实际包含的数据：

```bash
teap -o json result groups
teap -o json result groups 12345
```

动态返回包括实际 `_result.*` key、普通/场景设备曲线 inventory、曲线的 `curve_units`、分区及精确 bus-index。`curve_units` 中的 `name/coefficient/type` 直接来自当前 Core 响应；不要从静态常用列表推断某个结果一定包含某条曲线或单位。

读取精确 group：

```bash
teap -o json result get 12345 -g parameter
```

对于带单位的 JSON 结果，第一次不加 `--unit` 查询并读取 `available_units`；其中每个单位的值都等于 Core 原始 `data × coefficient`。选择精确名称后让 CLI 执行换算：

```bash
teap -o json result get 12345 -g _result.balance_df.neps_power -l --unit 兆瓦
teap -o json result get 12345 -g _result.balance_df.neps_electricity_monthly -l --unit 兆瓦时
```

普通 `json` 会返回 `unit.name/coefficient/applied=true`；未指定单位时返回 `unit.applied=false` 与 `available_units`，数据保持 Core 原值。`raw-json` 始终保持服务端原始响应，因此不能与 `--unit` 合用。`--unit` 也不与 `--to-csv-format` 合用，因为 CSV/XLSX 的换算和单位标题由 Core 控制。单位不存在或响应没有单位元数据时，`result_unit_unavailable` 为不可原样重试错误；按 hint 去掉 `--unit` 检查实际元数据，不得猜系数。

语法护栏：任务 ID 或服务端 `.tr` 路径是位置参数，`-g/--group` 必填；没有公开的 `-t` 或 `--task-record-id`。数字任务 ID 会先解析为 `result_path`，再读取结果，这也适用于 job type 301。需要后端过滤参数时使用 `--extra-param-json/--extra-param-file`。

常用中长期 group：

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

不要猜 `renewable`、`power_balance`、`consumption` 等自然语言 group。某个猜测 group 返回无法解析不代表 `.tr` 损坏；先运行动态 `result groups <source>`。`result get --to-list` 仍要求有效 group，不是列 group 的方法。

任意零基仿真时刻电力平衡、系统/分区工作位置图和设备曲线有专用命令：

```bash
teap -o json result balance-hour 12345 750
teap -o json result work-position 12345
teap -o json result work-position 12345 --zone "华东"
teap -o json result work-position 12345 --scenario
teap -o json result work-position 12345 --scenario --zone "华东"
teap -o json result curve 12345 wind Pwind_curt --metadata-only
teap -o json result curve 12345 wind Pwind_curt --device-index 7
teap -o json result curve 12345 wind Pwind_curt --device-index 7 --unit 万千瓦
```

设备索引先由 `--metadata-only` 获取；该请求返回名称和 index，不返回时序矩阵。不得猜 index，也不要为找索引先拉取全量设备曲线。

月度默认 `_result.balance_df.neps_power` 每月从完整时序中选取风险代表行：主排序为四位小数舍入后的 `power_balance` 升序；全系统主值相同时，再按缺口合计降序、负荷及运行特征排序。因此通常是当月最严重缺口，若没有缺口则是盈余/裕度最小时刻。它不是唯一可查询时刻。完整快速查询链路见 [result-query-recipes.md](result-query-recipes.md)。

需要 CSV 时按 `result get -h` 使用 `--to-csv-format` 和该命令的文件输出选项。

市场模拟结果不要套用中长期摘要路径。先运行：

```bash
teap -o json result market groups 12345
```

再按实际存在的电价、机组经济性和统计维度查询。完整语义和命令见 [market-results.md](market-results.md)。

## 按用户要求导出结果文件

结构化结果接口能够读取 `.tr` 中保存的 result key 和设备时序，普通结果分析必须使用这些服务端接口。只有用户明确要求取得、导出、交付、保存或在本地检查文件时才走本节。数据未暴露或后端无法解析时，报告已尝试的 group/curve 和接口限制；未经用户明确要求，不得自行下载 `.tr` 解析。

导入已有本地 `.tr`：

```bash
teap -o json result import-tr ./a.tr ./b.tr
```

导入结果只创建或登记结果对象，不把它当作可编辑 case。

下载 `.tr`：

```bash
teap -o json result download-tr 12345 -o ./result.tr
```

也可传明确的 result path。不要把 case path 传给 `download-tr`。

用户明确要求把结果内嵌输入模型导出到本地时：

```bash
teap -o json result download-tc 12345 -o ./result-case.tc
```

重新生成服务端报告不下载文件；用户明确要求本地报表或压缩包时再执行对应下载命令：

```bash
teap -o json result regenerate-reports 12345
teap -o json result download-summary 12345 --unit MW -o ./summary.xlsx
teap -o json result download-detail --task-id 12345 --task-id 12346 -o ./detail.zip
teap -o json result download-balance --result-path /server/a.tr -o ./balance.zip
```

批量 detail/balance 必须只选择 task ID 或 result path 一类来源，不能混用。result path 是 `.tr` 的服务端路径，不是 case path。

## 删除结果

只删除已完成且包含 `result_path` 的任务/结果记录：

```bash
teap -o json result delete 12345 --confirm
```

可以传多个 task ID。该命令删除完成结果、对应任务历史和关联日志，但保留源 `.tc` case；需要删除输入模型时使用 `case delete`。等待中、计算中、停止中、失败或没有 result path 的任务会返回 `result_not_deletable`，执行 hint 后停止，不要用另一个 ID 盲试。

## 运行方式筛选

对 short-term `.tr` 提取典型运行方式：

```bash
teap -o json result operation-modes scenarios
teap -o json result operation-modes run 12345 \
  --scenarios-file ./scenarios.json \
  --summary-output ./typical.xlsx --detail-output ./typical-detail.xlsx
```

场景 JSON 必须包含 `scenarios` 返回的全部分类键；不选择的分类传空数组。中长期或规划结果不支持该算法，服务端明确拒绝后不要改变 task ID 盲目重试。详细语义见 [algorithm-preprocessing.md](algorithm-preprocessing.md)。

禁止把 `.tr` 原始内容输出到模型上下文。不得使用 `cat/head/tail`、无界 `strings/grep`。确需离线诊断时，用受控解析器读取明确 key，并限制输出行数与字节数。

## 日志诊断

任务失败的默认入口：

```bash
teap -o json log analyze --task-id 12345
```

该命令优先使用用户级任务日志。只有明确需要且具备权限时才加全局日志：

```bash
teap -o json log analyze --task-id 12345 \
  --include-global --logs error.log,solver.log
```

定点工具：

```bash
teap -o json log list
teap -o json log tail error.log --lines 200
teap -o json log search infeasible --logs error.log,solver.log \
  --max-matches 50 --context 3
```

诊断顺序：状态 -> failure 摘要 -> task log analyze -> 必要的全局片段 -> 回读相关 sheet/parameter。不要先下载完整日志，也不要在没有证据时大范围改 case。

## 完成汇报

至少给出：服务地址、case 名/path、job type、task ID、状态、result path、检查过的 result group。失败时给出日志 finding 和下一步，不要声称已有结果。生成报告时再给出 `.docx` 路径。
